"""Tests for SQLite mirror: backfill, dual-write, embeddings parity, separation."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from database import Database, relational_db, interior_db, RELATIONAL_SCHEMA, INTERIOR_SCHEMA
from events import EventStore
from interior import InteriorStore


class BackfillTests(unittest.TestCase):
    """Backfill from JSONL into SQLite mirrors."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "relational").mkdir(parents=True)
        (self.root / "interior").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_events_jsonl(self, records: list[dict]) -> None:
        with (self.root / "events.jsonl").open("w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def test_backfill_idempotent(self) -> None:
        """Running backfill twice produces identical row counts."""
        self._write_events_jsonl([
            {"id": "e1", "role": "user", "content": "hi", "said_at": "2026-01-01T00:00:00Z"},
            {"kind": "privacy", "target_id": "e1", "sensitive": False, "source": "sensor", "said_at": "2026-01-01T00:00:01Z"},
            {"kind": "chat_boundary", "said_at": "2026-01-01T00:00:02Z"},
        ])
        # Import here so path is set
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        from backfill_sqlite import backfill_relational, backfill_interior

        rel = relational_db(self.root)
        intr = interior_db(self.root)

        c1 = backfill_relational(rel, self.root)
        c2 = backfill_relational(rel, self.root)
        self.assertEqual(c1, c2)

        ic1 = backfill_interior(intr, self.root)
        ic2 = backfill_interior(intr, self.root)
        self.assertEqual(ic1, ic2)

        rel.close()
        intr.close()

    def test_backfill_counts_match_source(self) -> None:
        """Row counts match JSONL line counts."""
        self._write_events_jsonl([
            {"id": "e1", "role": "user", "content": "a", "said_at": "2026-01-01T00:00:00Z"},
            {"id": "e2", "role": "assistant", "content": "b", "said_at": "2026-01-01T00:00:01Z"},
            {"kind": "privacy", "target_id": "e1", "sensitive": True, "source": "sensor", "said_at": "2026-01-01T00:00:02Z"},
            {"kind": "chat_boundary", "said_at": "2026-01-01T00:00:03Z"},
        ])
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        from backfill_sqlite import backfill_relational, verify

        rel = relational_db(self.root)
        rc = backfill_relational(rel, self.root)
        self.assertEqual(rc["events"], 2)
        self.assertEqual(rc["privacy_records"], 1)
        self.assertEqual(rc["chat_boundaries"], 1)

        mismatches = verify(rc, {}, self.root)
        self.assertEqual(mismatches, [])
        rel.close()


class DualWriteTests(unittest.TestCase):
    """Dual-write from stores to SQLite mirrors."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.rel = relational_db(self.root)
        self.intr = interior_db(self.root)
        self.store = EventStore(self.root, mirror=self.rel)
        self.interior = InteriorStore(self.root, mirror=self.intr)

    def tearDown(self) -> None:
        self.rel.close()
        self.intr.close()
        self.tmp.cleanup()

    def test_event_dual_write(self) -> None:
        ev = self.store.append("user", "test content", initial_sensitive=False)
        self.assertEqual(self.rel.count("events"), 1)
        row = self.rel.fetchone("SELECT * FROM events WHERE id = ?", (ev["id"],))
        self.assertIsNotNone(row)
        self.assertEqual(row["content"], "test content")
        self.assertEqual(row["role"], "user")

    def test_privacy_dual_write(self) -> None:
        ev = self.store.append("user", "x", initial_sensitive=False)
        self.store.append_privacy(ev["id"], True, "sensor", carry_after=2)
        self.assertEqual(self.rel.count("privacy_records"), 1)
        row = self.rel.fetchone("SELECT * FROM privacy_records WHERE target_id = ?", (ev["id"],))
        self.assertEqual(row["sensitive"], 1)
        self.assertEqual(row["carry_after"], 2)

    def test_chat_boundary_dual_write(self) -> None:
        self.store.append_chat_boundary()
        self.assertEqual(self.rel.count("chat_boundaries"), 1)

    def test_entity_observation_dual_write(self) -> None:
        ev = self.store.append("user", "about elliot", initial_sensitive=False)
        self.store.append_entity_observation("elliot", "Elliot", "mentioned", source_event_id=ev["id"])
        self.assertEqual(self.rel.count("entity_observations"), 1)

    def test_heartbeat_completion_dual_write(self) -> None:
        ev = self.store.append("user", "msg", initial_sensitive=False)
        self.store.append_heartbeat_completion("entities", ev["id"])
        self.assertEqual(self.rel.count("heartbeat_completions"), 1)

    def test_reflection_dual_write(self) -> None:
        ref = self.interior.append_reflection("a thought", source_event_id="src-1")
        self.assertEqual(self.intr.count("reflections"), 1)
        row = self.intr.fetchone("SELECT * FROM reflections WHERE id = ?", (ref["id"],))
        self.assertEqual(row["content"], "a thought")

    def test_waiting_message_dual_write(self) -> None:
        self.interior.set_waiting_message("hello")
        row = self.intr.fetchone("SELECT * FROM waiting_message WHERE id = 1")
        self.assertIsNotNone(row)
        self.assertEqual(row["content"], "hello")
        self.assertEqual(row["read"], 0)

        self.interior.clear_waiting_message()
        row = self.intr.fetchone("SELECT * FROM waiting_message WHERE id = 1")
        self.assertEqual(row["read"], 1)

    def test_mirror_failure_does_not_lose_jsonl(self) -> None:
        """A broken mirror must not prevent JSONL writes."""
        # Close mirror to make it error
        self.rel.close()
        # Force a broken connection
        broken_db = Database(self.root / "nonexistent" / "broken.db", RELATIONAL_SCHEMA)
        self.store._mirror = broken_db

        # This should still succeed (JSONL write) despite mirror error
        ev = self.store.append("user", "still saved", initial_sensitive=False)
        self.assertEqual(ev["content"], "still saved")

        # Verify JSONL has it
        events = self.store.history()
        found = [e for e in events if e.get("id") == ev["id"]]
        self.assertEqual(len(found), 1)


class EmbeddingsParityTests(unittest.TestCase):
    """Embeddings load from SQLite vs JSONL produces same result."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.rel = relational_db(self.root)
        (self.root / "relational").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.rel.close()
        self.tmp.cleanup()

    def test_embeddings_parity(self) -> None:
        emb_path = self.root / "relational" / "embeddings.jsonl"
        vectors = {f"ev-{i}": [float(i)] * 10 for i in range(5)}
        for eid, vec in vectors.items():
            with emb_path.open("a") as f:
                f.write(json.dumps({"event_id": eid, "vector": vec}) + "\n")
            self.rel.store_embedding_vector(eid, vec)

        # SQLite read
        store_with = EventStore(self.root, mirror=self.rel)
        from_sqlite = store_with._load_embeddings()

        # JSONL read
        store_without = EventStore(self.root)
        from_jsonl = store_without._load_embeddings()

        self.assertEqual(from_sqlite, from_jsonl)
        self.assertEqual(len(from_sqlite), 5)

    def test_sqlite_fallback_to_jsonl_on_empty_mirror(self) -> None:
        """If SQLite has no embeddings, falls back to JSONL."""
        emb_path = self.root / "relational" / "embeddings.jsonl"
        with emb_path.open("w") as f:
            f.write(json.dumps({"event_id": "e1", "vector": [1.0, 2.0]}) + "\n")

        # Mirror has no embeddings loaded
        store = EventStore(self.root, mirror=self.rel)
        result = store._load_embeddings()
        self.assertEqual(len(result), 1)
        self.assertEqual(result["e1"], [1.0, 2.0])


class SeparationTests(unittest.TestCase):
    """Relational and interior databases have no table overlap."""

    def test_no_table_overlap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = relational_db(root)
            intr = interior_db(root)
            # Access .conn to trigger schema creation
            _ = rel.conn
            _ = intr.conn
            overlap = rel.table_names() & intr.table_names()
            self.assertEqual(overlap, set(), f"Table overlap: {overlap}")
            rel.close()
            intr.close()

    def test_db_files_in_separate_directories(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = relational_db(root)
            intr = interior_db(root)
            _ = rel.conn
            _ = intr.conn
            self.assertIn("relational", str(rel.db_path))
            self.assertIn("interior", str(intr.db_path))
            self.assertNotEqual(rel.db_path, intr.db_path)
            rel.close()
            intr.close()


if __name__ == "__main__":
    unittest.main()
