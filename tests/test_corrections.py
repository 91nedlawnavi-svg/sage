"""Transcript correction regressions use temporary data and fake providers only."""

from pathlib import Path
import json
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from database import interior_db, relational_db
from events import EventStore, content_revision
from heartbeat import Heartbeat
from interior import InteriorStore
from router import RouterResult


class SequenceRouter:
    aliases = ("test",)

    def __init__(self, *replies: str) -> None:
        self.replies = iter(replies)

    def chat_with_messages(self, messages: list[dict[str, str]]) -> RouterResult:
        return RouterResult(next(self.replies))


class CorrectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_failed_correction_embedding_cannot_recall_old_meaning(self) -> None:
        class Embedder:
            def embed(self, text: str) -> list[float] | None:
                return None if text == "violet garden" else [1.0, 0.0]

        store = EventStore(self.root, Embedder())
        event = store.append("user", "seaside holiday", source="voice")
        store.append_transcript_correction(event["id"], "violet garden")

        self.assertEqual(store.history()[0]["content"], "violet garden")
        self.assertEqual(store.recall("ocean", fallback=False), [])

    def test_repeated_correction_retries_embedding_and_survives_reload(self) -> None:
        class Embedder:
            def __init__(self) -> None:
                self.correction_attempts = 0

            def embed(self, text: str) -> list[float] | None:
                if text == "violet garden":
                    self.correction_attempts += 1
                    return None if self.correction_attempts == 1 else [0.0, 1.0]
                return [1.0, 0.0]

        embedder = Embedder()
        store = EventStore(self.root, embedder)
        event = store.append("user", "seaside holiday", source="voice")
        store.append_transcript_correction(event["id"], "violet garden")
        self.assertEqual(store.recall("ocean", fallback=False), [])

        store.append_transcript_correction(event["id"], "violet garden")
        restarted = EventStore(self.root, embedder)

        self.assertEqual(restarted.recall("ocean", fallback=False), [])
        self.assertEqual(restarted._load_embeddings()[event["id"]], [0.0, 1.0])
        raw = store._read_records()
        self.assertEqual(raw[0]["content"], "seaside holiday")
        self.assertEqual(
            [record["content"] for record in raw if record.get("kind") == "transcript_correction"],
            ["violet garden", "violet garden"],
        )

    def test_late_stale_embedding_cannot_hide_an_earlier_current_revision(self) -> None:
        store = EventStore(self.root)
        event = store.append("user", "seaside holiday", source="voice")
        store.append_transcript_correction(event["id"], "violet garden")
        records = [
            {"event_id": event["id"], "vector": [1.0, 0.0],
             "content_revision": content_revision("seaside holiday")},
            {"event_id": event["id"], "vector": [0.0, 1.0],
             "content_revision": content_revision("violet garden")},
            {"event_id": event["id"], "vector": [1.0, 0.0],
             "content_revision": content_revision("seaside holiday")},
        ]
        store.embeddings_path.write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8",
        )

        self.assertEqual(store._load_embeddings()[event["id"]], [0.0, 1.0])

    def test_correction_reprocesses_an_already_extracted_entity(self) -> None:
        store = EventStore(self.root)
        interior = InteriorStore(self.root)
        voice = store.append("user", "Mara grows roses.", source="voice")
        router = SequenceRouter(
            '[{"entity_id":"mara","name":"Mara","observation":"grows roses"}]',
            '[{"entity_id":"mara","name":"Mara","observation":"grows rice"}]',
        )
        worker = Heartbeat(store, interior, router)

        worker._extract_entities_pass()
        store.append_transcript_correction(voice["id"], "Mara grows rice.")
        worker._extract_entities_pass()
        store.append_transcript_correction(voice["id"], "Mara grows rice.")
        worker._extract_entities_pass()

        store.append_transcript_correction(voice["id"], "Mara grows wheat.")
        Heartbeat(
            store,
            interior,
            SequenceRouter('[{"entity_id":"mara","name":"Mara","observation":"grows wheat"}]'),
        )._extract_entities_pass()

        restarted = EventStore(self.root)
        Heartbeat(restarted, InteriorStore(self.root), SequenceRouter())._extract_entities_pass()

        self.assertEqual(
            [entry["observation"] for entry in store.entity_observations()],
            ["grows roses", "grows rice", "grows wheat"],
        )
        self.assertEqual(len({entry["content_revision"] for entry in store.entity_observations()}), 3)

    def test_correction_before_entity_processing_uses_only_corrected_words(self) -> None:
        store = EventStore(self.root)
        voice = store.append("user", "Mara grows roses.", source="voice")
        store.append_transcript_correction(voice["id"], "Mara grows rice.")
        router = SequenceRouter(
            '[{"entity_id":"mara","name":"Mara","observation":"grows rice"}]',
        )

        Heartbeat(store, InteriorStore(self.root), router)._extract_entities_pass()

        self.assertEqual([entry["observation"] for entry in store.entity_observations()], ["grows rice"])
        self.assertEqual(store._read_records()[0]["content"], "Mara grows roses.")

    def test_correction_reprocesses_an_already_reflected_window(self) -> None:
        store = EventStore(self.root)
        interior = InteriorStore(self.root)
        store.append("user", "Context")
        voice = store.append("assistant", "Mara grows roses.", source="voice")
        router = SequenceRouter("wrong reflection", "revised reflection")
        worker = Heartbeat(store, interior, router)

        worker._reflection_pass()
        store.append_transcript_correction(voice["id"], "Mara grows rice.")
        worker._reflection_pass()
        store.append_transcript_correction(voice["id"], "Mara grows rice.")
        worker._reflection_pass()

        restarted = EventStore(self.root)
        Heartbeat(restarted, InteriorStore(self.root), SequenceRouter())._reflection_pass()

        self.assertEqual(
            [entry["content"] for entry in interior.list_reflections()],
            ["wrong reflection", "revised reflection"],
        )

    def test_revised_self_reflection_remains_available_to_identity_proposals(self) -> None:
        store = EventStore(self.root)
        interior = InteriorStore(self.root)
        store.append("user", "Context")
        voice = store.append("assistant", "I always hedge.", source="voice")
        router = SequenceRouter(
            "SELF: I always hedge.",
            "I hedge in every reply.",
            "SELF: I sometimes answer directly.",
            "I sometimes answer directly.",
        )
        worker = Heartbeat(store, interior, router)

        worker._reflection_pass()
        worker._identity_proposal_pass()
        store.append_transcript_correction(voice["id"], "I sometimes answer directly.")
        worker._reflection_pass()
        worker._identity_proposal_pass()

        identity = interior.list_identity()
        self.assertEqual([entry["claim"] for entry in identity], [
            "I hedge in every reply.",
            "I sometimes answer directly.",
        ])
        self.assertEqual(len({entry["evidence"][0] for entry in identity}), 2)

    def test_revision_metadata_dual_writes_and_rebuilds_exactly(self) -> None:
        class Embedder:
            def embed(self, text: str) -> list[float]:
                return [1.0, 0.0]

        relational = relational_db(self.root)
        interior_mirror = interior_db(self.root)
        self.addCleanup(relational.close)
        self.addCleanup(interior_mirror.close)
        store = EventStore(self.root, Embedder(), mirror=relational)
        interior = InteriorStore(self.root, mirror=interior_mirror)
        voice = store.append("user", "Mara grows roses.", source="voice")
        old_revision = content_revision("Mara grows roses.")
        store.append_entity_observation(
            "mara", "Mara", "grows roses", source_event_id=voice["id"],
            content_revision=old_revision,
        )
        store.append_heartbeat_completion(
            "entities", voice["id"], content_revision=old_revision,
        )
        interior.append_reflection(
            "old reflection", source_event_id=voice["id"], content_revision=old_revision,
        )

        store.append_transcript_correction(voice["id"], "Mara grows rice.")
        new_revision = content_revision("Mara grows rice.")
        store.append_entity_observation(
            "mara", "Mara", "grows rice", source_event_id=voice["id"],
            content_revision=new_revision,
        )
        store.append_heartbeat_completion(
            "entities", voice["id"], content_revision=new_revision,
        )
        interior.append_reflection(
            "new reflection", source_event_id=voice["id"], content_revision=new_revision,
        )

        self.assertEqual(
            relational.fetchone(
                "SELECT content_revision FROM embeddings WHERE event_id = ?", (voice["id"],),
            )["content_revision"],
            new_revision,
        )
        self.assertEqual(relational.count("entity_observations"), 2)
        self.assertEqual(
            relational.fetchone(
                "SELECT content_revision FROM heartbeat_completions WHERE source_event_id = ?",
                (voice["id"],),
            )["content_revision"],
            new_revision,
        )
        self.assertEqual(interior_mirror.count("reflections"), 2)

        from mirror_rebuild import verify
        self.assertEqual(verify({"present": 1}, {"present": 1}, self.root), [])

    def test_existing_mirrors_gain_revision_columns(self) -> None:
        relational_path = self.root / "relational" / "relational.db"
        relational_path.parent.mkdir(parents=True)
        connection = sqlite3.connect(relational_path)
        connection.executescript("""
            CREATE TABLE entity_observations (
                id INTEGER PRIMARY KEY, entity_id TEXT, name TEXT, observation TEXT,
                said_at TEXT, source_event_id TEXT
            );
            CREATE TABLE heartbeat_completions (
                id INTEGER PRIMARY KEY, stage TEXT, source_event_id TEXT, said_at TEXT
            );
            CREATE TABLE embeddings (event_id TEXT PRIMARY KEY, vector TEXT);
        """)
        connection.close()

        interior_path = self.root / "interior" / "interior.db"
        interior_path.parent.mkdir(parents=True)
        connection = sqlite3.connect(interior_path)
        connection.execute(
            "CREATE TABLE reflections (id TEXT PRIMARY KEY, content TEXT, said_at TEXT, "
            "category TEXT, source_event_id TEXT)",
        )
        connection.close()

        relational = relational_db(self.root)
        interior_mirror = interior_db(self.root)
        self.addCleanup(relational.close)
        self.addCleanup(interior_mirror.close)

        for table in ("entity_observations", "heartbeat_completions", "embeddings"):
            self.assertIn(
                "content_revision",
                {row["name"] for row in relational.fetchall(f"PRAGMA table_info({table})")},
            )
        self.assertIn(
            "content_revision",
            {row["name"] for row in interior_mirror.fetchall("PRAGMA table_info(reflections)")},
        )


if __name__ == "__main__":
    unittest.main()
