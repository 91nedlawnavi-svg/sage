"""SAGE-021 hostile routing probes. Temporary data and mocked providers only.

Run: python3 -m unittest discover -s tools/audit_sage021 -p test_routing.py -v
Assertions describe intended behavior; failures identify baseline defects.
"""

from concurrent.futures import ThreadPoolExecutor
from http.client import IncompleteRead
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from events import EventStore
from interior import InteriorStore
from router import RouterClient, RouterStream
from search import search
from web import SageHandler


def sse(*chunks, usage=False):
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": chunk}}]}) + "\n\n"
        for chunk in chunks
    ]
    if usage:
        lines.append('data: {"choices": [], "usage": {"total_tokens": 7}}\n\n')
    lines.append("data: [DONE]\n\n")
    return io.BytesIO("".join(lines).encode())


class CaptureRouter:
    aliases = ("model-a", "model-b")
    last_alias = "model-a"

    def __init__(self, barrier=None):
        self.prompts = []
        self.barrier = barrier

    def stream_with_messages(self, messages, **kwargs):
        self.prompts.append(messages)

        def chunks():
            if self.barrier:
                self.barrier.wait(timeout=5)
            yield "Complete answer."
            yield ""

        return RouterStream(chunks(), actual_alias="model-a")


class RoutingAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sage-routing-audit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root)

    def handler(self, body, router=None):
        handler = SageHandler.__new__(SageHandler)
        handler.server = SimpleNamespace(
            store=self.store,
            router=router or CaptureRouter(),
            interior=InteriorStore(self.root),
        )
        handler._json_body = Mock(return_value=body)
        handler._begin_stream = Mock()
        handler._write_stream_event = Mock()
        handler._json = Mock()
        handler._decide_search = Mock(return_value=None)
        handler.wfile = io.BytesIO()
        return handler

    def test_switch_during_search_keeps_original_session_tail(self):
        self.store.append("user", "orchard apple inventory")
        self.store.append("assistant", "orchard pears tally")
        original = self.store.current_session_id
        router = CaptureRouter()
        handler = self.handler({"message": "continue"}, router)

        def switch_session(*args):
            self.store.append_chat_boundary()
            self.store.append("user", "ocean coral survey")
            self.store.append("assistant", "ocean jellyfish census")

        handler._decide_search.side_effect = switch_session
        handler._chat()
        contents = [message["content"] for message in router.prompts[0] if message["role"] != "system"]
        self.assertIn("orchard apple inventory", contents)
        self.assertIn("orchard pears tally", contents)
        self.assertEqual(self.store.history()[-1]["session_id"], original)

    def test_concurrent_retries_generate_only_one_answer(self):
        accepted = self.store.append("user", "Please finish the answer")
        router = CaptureRouter(threading.Barrier(2))
        handlers = [self.handler({"retry_event_id": accepted["id"]}, router) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(handler._chat) for handler in handlers]
            for future in futures:
                future.result(timeout=8)
        history = self.store.history()
        self.assertEqual(len([event for event in history if event["role"] == "user"]), 1)
        self.assertEqual(len([event for event in history if event["role"] == "assistant"]), 1)

    def test_correction_with_failed_embedding_cannot_recall_old_meaning(self):
        class Embedder:
            def embed(self, text):
                if text == "violet garden":
                    return None
                return [1.0, 0.0]

        self.store = EventStore(self.root, Embedder())
        event = self.store.append("user", "seaside holiday", source="voice")
        self.store.append_transcript_correction(event["id"], "violet garden")
        self.assertEqual(self.store.history()[0]["content"], "violet garden")
        self.assertEqual(self.store.recall("ocean", fallback=False), [])

    def test_successful_correction_embedding_replaces_old_meaning(self):
        class Embedder:
            def embed(self, text):
                return [0.0, 1.0] if text == "violet garden" else [1.0, 0.0]

        self.store = EventStore(self.root, Embedder())
        event = self.store.append("user", "seaside holiday", source="voice")
        self.store.append_transcript_correction(event["id"], "violet garden")
        self.assertEqual(self.store.history()[0]["content"], "violet garden")
        self.assertEqual(self.store.recall("ocean", fallback=False), [])

    def test_new_assistant_search_shaped_text_remains_history(self):
        self.store.append("user", "Show the literal legacy search record format")
        event = self.store.append("assistant", "[Web search: demo]\nSources: sample", model="model-a")
        self.assertIn(event["id"], [item["id"] for item in self.store.history()])

    def test_whitespace_stream_is_failure_and_saves_no_assistant(self):
        accepted = self.store.append("user", "Hello")
        router = RouterClient("model-a")
        handler = self.handler({}, router)
        with patch("router.urlopen", return_value=sse(" \n\t")):
            handler._stream_reply(
                router.stream("Hello"), persist_reply=True,
                session_id=accepted["session_id"], event_id=accepted["id"],
            )
        self.assertEqual([event["role"] for event in self.store.history()], ["user"])
        self.assertIn("model_error", [call.args[0] for call in handler._write_stream_event.call_args_list])

    def test_stream_usage_metadata_does_not_discard_complete_answer(self):
        router = RouterClient("model-a")
        with patch("router.urlopen", return_value=sse("Complete answer.", usage=True)):
            stream = router.stream("Hello")
            self.assertEqual(list(stream), ["Complete answer.", ""])
            self.assertEqual(stream.actual_alias, "model-a")

    def test_malformed_chat_schema_falls_back(self):
        router = RouterClient(("model-a", "model-b"))
        malformed = io.BytesIO(b'{"choices": [{"message": null}]}')
        valid = io.BytesIO(b'{"choices": [{"message": {"content": "Recovered"}}]}')
        with patch("router.urlopen", side_effect=[malformed, valid]):
            result = router.chat("Hello")
        self.assertEqual(result.reply, "Recovered")
        self.assertEqual(result.model, "model-b")

    def test_unclosed_reasoning_only_chat_falls_back(self):
        router = RouterClient(("model-a", "model-b"))
        reasoning = io.BytesIO(b'{"choices": [{"message": {"content": "<think>private reasoning"}}]}')
        valid = io.BytesIO(b'{"choices": [{"message": {"content": "Recovered"}}]}')
        with patch("router.urlopen", side_effect=[reasoning, valid]):
            result = router.chat("Hello")
        self.assertEqual(result.reply, "Recovered")
        self.assertEqual(result.model, "model-b")

    def test_malformed_stream_schema_falls_back_before_visible_text(self):
        router = RouterClient(("model-a", "model-b"))
        malformed = io.BytesIO(b'data: {"choices": [{"delta": null}]}\n\n')
        with patch("router.urlopen", side_effect=[malformed, sse("Recovered")]):
            stream = router.stream("Hello")
            self.assertEqual(list(stream), ["Recovered", ""])
            self.assertEqual(stream.actual_alias, "model-b")

    def test_truncated_search_fails_soft(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = IncompleteRead(b'{"results": [')
        with patch("search.urlopen", return_value=response):
            self.assertEqual(search("weather"), [])

    def test_malformed_search_schema_fails_soft(self):
        with patch("search.urlopen", return_value=io.BytesIO(b'{"results": [null]}')):
            self.assertEqual(search("weather"), [])

    def test_truncated_stream_does_not_save_partial_reply(self):
        accepted = self.store.append("user", "Hello")
        router = RouterClient("model-a")
        handler = self.handler({}, router)
        response = io.BytesIO(b'data: {"choices": [{"delta": {"content": "Partial"}}]}\n\n')
        with patch("router.urlopen", return_value=response):
            handler._stream_reply(
                router.stream("Hello"), persist_reply=True,
                session_id=accepted["session_id"], event_id=accepted["id"],
            )
        self.assertEqual([event["role"] for event in self.store.history()], ["user"])
        self.assertIn("model_error", [call.args[0] for call in handler._write_stream_event.call_args_list])

    def test_explicit_stream_failure_never_falls_back(self):
        router = RouterClient(("model-a", "model-b"))
        with patch("router.urlopen", side_effect=OSError("provider unavailable")) as request:
            stream = router.stream_with_messages([{"role": "user", "content": "Hi"}], alias="model-a")
            self.assertEqual(list(stream), [])
        self.assertEqual(stream.attempted_aliases, ["model-a"])
        self.assertIsNone(stream.actual_alias)
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
