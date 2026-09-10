from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from database import interior_db, relational_db
from deletion import DeletionError, build_deletion_plan, execute_deletion
from events import EventStore
from interior import InteriorStore
from provenance import provenance


class SessionDeletionTests(unittest.TestCase):
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

    def test_preview_and_delete_remove_direct_and_derived_records(self) -> None:
        voice = self.store.append("user", "old voice", source="voice", call_id="call", turn_id="turn")
        self.store.append("assistant", "old answer")
        self.store.append_transcript_correction(voice["id"], "corrected voice")
        self.store.append_entity_observation("old", "Old", "derived", source_event_id=voice["id"])
        self.store.append_heartbeat_completion("entities", voice["id"])
        proof = provenance(events=[voice])
        self.store.append_search_record("old query", [], "conversation", voice["id"], provenance=proof)
        self.interior.append_reflection("old reflection", source_event_id=voice["id"], provenance=proof)
        self.interior.set_waiting_message("old waiting", source_event_id=voice["id"], provenance=proof)
        self.store.append_chat_boundary()
        keep = self.store.append("user", "keep this")

        plan = build_deletion_plan(self.store, self.interior, voice["session_id"])
        self.assertEqual(plan.unresolved, [])
        self.assertEqual(plan.counts["events"], 2)
        with self.assertRaises(DeletionError):
            execute_deletion(self.store, self.interior, self.rel, self.intr, plan, "delete")
        execute_deletion(self.store, self.interior, self.rel, self.intr, plan, "DELETE")

        self.assertEqual([event["id"] for event in self.store.history()], [keep["id"]])
        restarted = EventStore(self.root)
        self.assertEqual([event["id"] for event in restarted.history()], [keep["id"]])
        self.assertEqual(self.store.recall("old voice", fallback=False), [])
        for table in ("events", "event_sessions", "voice_event_context", "transcript_corrections", "entity_observations", "heartbeat_completions", "search_records", "embeddings"):
            self.assertEqual(self.rel.count(table), 1 if table in {"events", "event_sessions"} else 0, table)
        self.assertEqual(self.intr.count("reflections"), 0)
        self.assertEqual(self.intr.count("waiting_message"), 0)

    def test_interrupted_staging_changes_nothing(self) -> None:
        event = self.store.append("user", "do not remove")
        before = (self.store.path.read_bytes(), self.store.entities_path.exists())
        plan = build_deletion_plan(self.store, self.interior, event["session_id"])
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            execute_deletion(
                self.store, self.interior, self.rel, self.intr, plan, "DELETE",
                before_commit=lambda: (_ for _ in ()).throw(RuntimeError("interrupted")),
            )
        self.assertEqual(self.store.path.read_bytes(), before[0])
        self.assertEqual(self.store.history()[0]["id"], event["id"])


if __name__ == "__main__":
    unittest.main()
