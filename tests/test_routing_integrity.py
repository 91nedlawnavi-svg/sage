"""Regression tests for foreground session and retry integrity."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import io
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import EventStore
from interior import InteriorStore
from router import RouterStream
from web import SageHandler


class CaptureRouter:
    aliases = ("model-a",)
    last_alias = "model-a"

    def __init__(self, *, blocked: bool = False) -> None:
        self.prompts: list[list[dict[str, str]]] = []
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()

    def stream_with_messages(self, messages, **kwargs):
        self.prompts.append(messages)
        self.calls += 1
        self.started.set()

        def chunks():
            self.release.wait(timeout=2)
            yield "Complete answer."
            yield ""

        return RouterStream(chunks(), actual_alias="model-a")


class RoutingIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="sage-routing-")
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.store = EventStore(self.root)

    def handler(self, body, router):
        handler = SageHandler.__new__(SageHandler)
        handler.server = SimpleNamespace(
            store=self.store,
            router=router,
            interior=InteriorStore(self.root),
        )
        handler._json_body = Mock(return_value=body)
        handler._begin_stream = Mock()
        handler._write_stream_event = Mock()
        handler._json = Mock()
        handler._decide_search = Mock(return_value=None)
        handler.wfile = io.BytesIO()
        return handler

    def test_switch_during_search_keeps_original_session_tail(self) -> None:
        self.store.append("user", "orchard apple inventory")
        self.store.append("assistant", "orchard pears tally")
        original_session_id = self.store.current_session_id
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
        self.assertNotIn("ocean coral survey", contents)
        self.assertNotIn("ocean jellyfish census", contents)
        self.assertEqual(self.store.history()[-1]["session_id"], original_session_id)

    def test_concurrent_retries_generate_and_save_only_one_answer(self) -> None:
        accepted = self.store.append("user", "Please finish the answer")
        router = CaptureRouter(blocked=True)
        first = self.handler({"retry_event_id": accepted["id"]}, router)
        second = self.handler({"retry_event_id": accepted["id"]}, router)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(first._chat)
            self.assertTrue(router.started.wait(timeout=1))
            second_future = pool.submit(second._chat)
            try:
                second_future.result(timeout=0.5)
            except TimeoutError:
                pass
            router.release.set()
            first_future.result(timeout=3)
            second_future.result(timeout=3)

        history = self.store.history()
        self.assertEqual(router.calls, 1)
        self.assertEqual(len([event for event in history if event["role"] == "user"]), 1)
        self.assertEqual(len([event for event in history if event["role"] == "assistant"]), 1)
        second._json.assert_called_once_with(409, {"error": "This message is already being retried."})

    def test_failed_retry_releases_claim_for_another_attempt(self) -> None:
        accepted = self.store.append("user", "Please try again")
        failed_router = CaptureRouter()
        failed = self.handler({"retry_event_id": accepted["id"]}, failed_router)
        failed_router.stream_with_messages = Mock(return_value=RouterStream(iter(()), actual_alias=None))

        failed._chat()
        succeeding_router = CaptureRouter()
        self.handler({"retry_event_id": accepted["id"]}, succeeding_router)._chat()

        self.assertEqual(
            [(event["role"], event["content"]) for event in self.store.history()],
            [("user", "Please try again"), ("assistant", "Complete answer.")],
        )

    def test_new_assistant_search_shaped_text_remains_history_and_recall(self) -> None:
        self.store.append("user", "Show the literal legacy search record format")
        event = self.store.append("assistant", "[Web search: demo]\nSources: sample", model="model-a")

        self.assertIn(event["id"], [item["id"] for item in self.store.history()])
        self.assertIn(event["id"], [item["id"] for item in self.store.recall("demo sample", fallback=False)])


if __name__ == "__main__":
    unittest.main()
