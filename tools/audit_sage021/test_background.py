"""SAGE-021 background probes. Temporary data and mocked providers only.

Run: python3 -m unittest discover -s tools/audit_sage021 -p test_background.py -v
Assertions describe intended behavior; baseline failures are audit evidence.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from events import EventStore
from heartbeat import Heartbeat
from interior import InteriorStore
from provenance import provenance
from router import RouterResult
from search import SearchResult


class SequenceRouter:
    aliases = ("audit-mock",)

    def __init__(self, *replies):
        self.replies = iter(replies)
        self.messages = []

    def chat_with_messages(self, messages):
        self.messages.append(messages)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return RouterResult(reply)


class BackgroundAudit(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="sage021-background-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = EventStore(self.root)
        self.interior = InteriorStore(self.root)

    def test_real_metabolism_failures_remain_retryable(self):
        gaps = '[{"gap":"missing offset","query":"WIB offset"}]'
        results = [SearchResult(title="WIB", snippet="UTC+7", url="https://example.invalid")]
        cases = {
            "gap_provider_failure": ([None], None),
            "gap_provider_exception": ([OSError("router unavailable")], None),
            "gap_invalid_json": (["truncated json"], None),
            "search_failure": ([gaps], OSError("search unavailable")),
            "digest_provider_failure": ([gaps, None], None),
            "reach_provider_failure": ([gaps, "Learned UTC+7.", None], None),
        }
        for name, (replies, search_error) in cases.items():
            with self.subTest(stage=name):
                stage_root = self.root / name
                store = EventStore(stage_root)
                interior = InteriorStore(stage_root)
                event = store.append("user", "What offset is WIB?")
                worker = Heartbeat(store, interior, SequenceRouter(*replies), metabolism_delay=0)
                with patch("metabolism.search", return_value=results, side_effect=search_error):
                    worker._metabolism_pass()
                self.assertNotIn(
                    event["id"], store.heartbeat_completed("metabolism"),
                    "Transient stage failure was permanently recorded as completed",
                )

    def test_valid_no_gap_response_completes_without_retry(self):
        event = self.store.append("user", "Hello.")
        router = SequenceRouter("[]")
        worker = Heartbeat(self.store, self.interior, router, metabolism_delay=0)
        worker._metabolism_pass()
        worker._metabolism_pass()
        self.assertIn(event["id"], self.store.heartbeat_completed("metabolism"))
        self.assertEqual(len(router.messages), 1)

    def test_invalid_entity_response_remains_retryable(self):
        for index, reply in enumerate(('{"entities":[]}', '[{"unexpected":"shape"}]')):
            with self.subTest(reply=reply):
                stage_root = self.root / str(index)
                store = EventStore(stage_root)
                event = store.append("user", "My friend Mara is a gardener.")
                worker = Heartbeat(store, InteriorStore(stage_root), SequenceRouter(reply))
                worker._extract_entities_pass()
                self.assertNotIn(
                    event["id"], store.heartbeat_completed("entities"),
                    "Wrong provider response schema silently consumes the event",
                )

    def test_valid_empty_entity_response_completes(self):
        event = self.store.append("user", "Hello.")
        worker = Heartbeat(self.store, self.interior, SequenceRouter("[]"))
        worker._extract_entities_pass()
        self.assertIn(event["id"], self.store.heartbeat_completed("entities"))

    def test_voice_correction_reaches_already_processed_entity_input(self):
        voice = self.store.append("user", "Mara grows roses.", source="voice")
        wrong_fact = '[{"entity_id":"mara","name":"Mara","observation":"grows roses"}]'
        corrected_fact = '[{"entity_id":"mara","name":"Mara","observation":"grows rice"}]'
        router = SequenceRouter(wrong_fact, corrected_fact)
        worker = Heartbeat(self.store, self.interior, router)
        worker._extract_entities_pass()
        corrected = "Mara grows rice."
        self.store.append_transcript_correction(voice["id"], corrected)
        worker._extract_entities_pass()
        self.assertTrue(
            any("grows rice" in entry["observation"] for entry in self.store.entity_observations()),
            "Correction never revises an already-processed wrong entity observation",
        )

    def test_correction_before_processing_uses_corrected_words(self):
        voice = self.store.append("user", "Mara grows roses.", source="voice")
        self.store.append_transcript_correction(voice["id"], "Mara grows rice.")
        router = SequenceRouter("[]")
        Heartbeat(self.store, self.interior, router)._extract_entities_pass()
        self.assertIn("Mara grows rice.", router.messages[0][0]["content"])
        self.assertNotIn("Mara grows roses.", router.messages[0][0]["content"])

    def test_concurrent_identity_proposals_consume_evidence_once(self):
        event = self.store.append("user", "You promised to stop repeating that opener.")
        self.interior.append_reflection(
            "I repeated the opener after promising to stop.", "self",
            source_event_id=event["id"], provenance=provenance(events=[event]),
        )
        rendezvous = Barrier(2)

        class BlockingRouter:
            aliases = ("audit-mock",)

            def chat_with_messages(self, messages):
                rendezvous.wait(timeout=5)
                return RouterResult("I repeat openers despite agreeing to stop.")

        workers = [Heartbeat(self.store, self.interior, BlockingRouter()) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(worker._identity_proposal_pass) for worker in workers]
            for task in pending:
                task.result(timeout=8)
        self.assertEqual(
            len(self.interior.list_identity()), 1,
            "Concurrent workers propose the exact same evidence twice",
        )

    def test_transitive_provenance_preserves_unknown_and_all_known_sources(self):
        event_a = {"id": "event-a"}
        event_b = {"id": "event-b"}
        known = {"provenance": provenance(events=[event_a])}
        self.assertEqual(
            provenance(events=[event_b], records=[known]),
            {"version": 1, "event_ids": ["event-a", "event-b"], "complete": True},
        )
        self.assertEqual(
            provenance(events=[event_b], records=[known, {}]),
            {"version": 1, "event_ids": ["event-a", "event-b"], "complete": False},
        )


if __name__ == "__main__":
    unittest.main()
