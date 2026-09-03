from __future__ import annotations

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event as ThreadEvent, Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import EventStore
from interior import InteriorStore
from router import ROUTER_BASE_URL, RouterClient, RouterResult
from sage import SENSITIVE_ACKNOWLEDGEMENT, ROUTER_FAILURE, build_router_messages, handle_message, load_directive
from heartbeat import Heartbeat, parse_reflection
from web import SageServer


class FakeRouter(BaseHTTPRequestHandler):
    status = 200
    response: object = {"choices": [{"message": {"content": "Hello."}}]}
    request_body: dict[str, object] | None = None
    truncate_response = False
    stream_chunks = ["Hel", "lo."]
    truncate_stream = False
    fail_models: set[str] = set()
    seen_models: list[str] = []
    stream_gate: ThreadEvent | None = None

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        type(self).request_body = json.loads(self.rfile.read(length))
        type(self).seen_models.append(type(self).request_body["model"])
        if type(self).request_body["model"] in type(self).fail_models:
            self.send_response(503)
            self.end_headers()
            return
        self.send_response(type(self).status)
        if type(self).request_body.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for index, chunk in enumerate(type(self).stream_chunks):
                self.wfile.write(f'data: {{"choices":[{{"delta":{{"content":{json.dumps(chunk)}}}}}]}}\n\n'.encode())
                self.wfile.flush()
                if index == 0 and type(self).stream_gate is not None:
                    type(self).stream_gate.wait(2)
            if not type(self).truncate_stream:
                self.wfile.write(b"data: [DONE]\n\n")
            return
        self.send_header("Content-Type", "application/json")
        body = json.dumps(type(self).response).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body[:-1] if type(self).truncate_response else body)

    def log_message(self, format: str, *args: object) -> None:
        return


class FakeScribe:
    def __init__(self, reflection_reply: str = "A reflection about the current conversation.") -> None:
        self.messages: list[list[dict[str, str]]] = []
        self.reflection_reply = reflection_reply

    def chat_with_messages(self, messages: list[dict[str, str]]) -> RouterResult:
        self.messages.append(messages)
        if messages[0]["content"].startswith("Extract key durable entities"):
            return RouterResult("[]")
        return RouterResult(self.reflection_reply)


class DeadRouter:
    aliases = ("dead-alias",)

    def chat_with_messages(self, messages: list[dict[str, str]]) -> RouterResult:
        return RouterResult(reply=None)


class FailingPrivacyStore(EventStore):
    def append_privacy(self, *args: object, **kwargs: object) -> object:
        raise OSError("privacy metadata unavailable")

def read_stream(response: object) -> list[dict[str, str]]:
    return [json.loads(line) for line in response.read().decode().splitlines()]


class FoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeRouter.status = 200
        FakeRouter.response = {"choices": [{"message": {"content": "Hello."}}]}
        FakeRouter.request_body = None
        FakeRouter.truncate_response = False
        FakeRouter.stream_chunks = ["Hel", "lo."]
        FakeRouter.truncate_stream = False
        FakeRouter.fail_models = set()
        FakeRouter.seen_models = []
        FakeRouter.stream_gate = None
        self.server = ThreadingHTTPServer(("localhost", 0), FakeRouter)
        self.thread = Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base_url = f"http://localhost:{self.server.server_port}"
        self.temporary_directory = TemporaryDirectory()
        self.data_root = Path(self.temporary_directory.name)
        self.store = EventStore(self.data_root)
        self.interior = InteriorStore(self.data_root)
        self.router = RouterClient("free-tier-alias", self.base_url)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temporary_directory.cleanup()

    def test_recall_excludes_non_query_matches(self) -> None:
        self.store.append("user", "Noisy weather update", initial_sensitive=False)
        self.store.append("assistant", "Sunny tomorrow")
        self.store.append("user", "I never told anyone about this confession", initial_sensitive=False)

        self.store.append("assistant", "Thanks for sharing")
        self.store.append("user", "I need advice", initial_sensitive=False)

        store = EventStore(self.store.data_root)
        self.assertEqual(
            [(event["role"], event["content"]) for event in store.recall("advice")],
            [("user", "I need advice")],
        )

    def test_recall_prefers_exact_match_over_keyword_ties(self) -> None:
        self.store.append("user", "Need a cup of tea and a sandwich", initial_sensitive=False)
        self.store.append("user", "Need help with the tea recipe", initial_sensitive=False)

        recalled = [(event["role"], event["content"]) for event in self.store.recall("need tea")]
        self.assertEqual(
            recalled,
            [
                ("user", "Need a cup of tea and a sandwich"),
                ("user", "Need help with the tea recipe"),
            ],
        )
        self.assertEqual(recalled[0][1], "Need a cup of tea and a sandwich")
        self.assertEqual(recalled[1][1], "Need help with the tea recipe")

    def test_recall_ranks_higher_overlap_and_term_frequency_first(self) -> None:
        self.store.append("user", "We bought apples and oranges for lunch", initial_sensitive=False)
        self.store.append("user", "Apples are great, I love green apples and red apples", initial_sensitive=False)
        self.store.append("user", "Just talking about oranges", initial_sensitive=False)

        recalled = self.store.recall("apples apples", limit=2)
        self.assertEqual(len(recalled), 2)
        self.assertEqual(recalled[0]["content"], "Apples are great, I love green apples and red apples")
        self.assertEqual(recalled[1]["content"], "We bought apples and oranges for lunch")

    def test_recall_treats_stopword_only_query_as_context_fallback(self) -> None:
        self.store.append("user", "First topic", initial_sensitive=False)
        self.store.append("assistant", "Answer")

        self.assertEqual(
            [(event["role"], event["content"]) for event in self.store.recall("the and a")],
            [("user", "First topic"), ("assistant", "Answer")],
        )

    def test_recall_excludes_sensitive_events(self) -> None:
        self.store.append("user", "open topic", initial_sensitive=False)
        hidden = self.store.append("user", "I never told anyone about this", initial_sensitive=False)
        self.store.append_privacy(hidden["id"], True, "sensor")
        self.store.append("assistant", "ack")

        self.assertEqual(
            [(event["role"], event["content"]) for event in self.store.recall("topic")],
            [("user", "open topic")],
        )

    def test_success_persists_separate_utc_events_and_routes_alias(self) -> None:
        reply = handle_message("Hello Sage", self.store, self.router)

        self.assertEqual(reply, "Hello.")
        self.assertEqual(FakeRouter.request_body["model"], "free-tier-alias")
        self.assertEqual(FakeRouter.request_body["messages"][0], {"role": "system", "content": load_directive()})
        self.assertEqual(FakeRouter.request_body["messages"][-1], {"role": "user", "content": "Hello Sage"})
        events = EventStore(self.store.data_root).read_all()
        self.assertEqual([(event["role"], event["content"]) for event in events], [("user", "Hello Sage"), ("assistant", "Hello.")])
        for event in events:
            self.assertEqual(datetime.fromisoformat(event["said_at"].replace("Z", "+00:00")).utcoffset().total_seconds(), 0)

    def test_prompt_recall_uses_recent_exchange_as_its_cue(self) -> None:
        self.store.append("user", "I keep buying potatoes even when I plan to cook something else", initial_sensitive=False)
        self.store.append("assistant", "You seem to like having them around.")

        messages = build_router_messages("I made them again tonight", self.store, directive=load_directive())

        self.assertEqual(messages[0], {"role": "system", "content": load_directive()})
        self.assertIn({"role": "user", "content": "I keep buying potatoes even when I plan to cook something else"}, messages)
        self.assertEqual(messages[-1], {"role": "user", "content": "I made them again tonight"})

    def test_prompt_context_keeps_recent_turns_and_adds_older_recall(self) -> None:
        self.store.append("user", "I want to learn pottery this year", initial_sensitive=False)
        self.store.append("assistant", "That sounds like a good creative outlet.")
        self.store.append("user", "Recent one", initial_sensitive=False)
        self.store.append("assistant", "Recent answer one")
        self.store.append("user", "Recent two", initial_sensitive=False)
        self.store.append("assistant", "Recent answer two")

        messages = build_router_messages("I tried pottery today", self.store, max_context=6)

        self.assertEqual(
            [message["content"] for message in messages],
            [
                "I want to learn pottery this year",
                "Recent one",
                "Recent answer one",
                "Recent two",
                "Recent answer two",
                "I tried pottery today",
            ],
        )

    def test_new_chat_preserves_old_events_for_recall_after_restart(self) -> None:
        self.store.append("user", "I keep buying potatoes", initial_sensitive=False)
        self.store.append("assistant", "They are a reliable default.")
        self.store.append_chat_boundary()
        reopened = EventStore(self.store.data_root)

        self.assertEqual(reopened.visible_history(), [])
        self.assertEqual(len(reopened.read_all()), 2)
        self.assertEqual(reopened.recall("potatoes", fallback=False)[0]["content"], "I keep buying potatoes")
        self.assertIn("chat_boundary", reopened.path.read_text())

        messages = build_router_messages("I made potatoes again", reopened)

        self.assertIn({"role": "user", "content": "I keep buying potatoes"}, messages)
        self.assertEqual(messages[-1], {"role": "user", "content": "I made potatoes again"})
        self.assertEqual(build_router_messages("Unrelated weather update", reopened), [{"role": "user", "content": "Unrelated weather update"}])

    def test_chat_sends_contradictory_public_history_without_sensitive_match(self) -> None:
        self.store.append("user", "I love hosting friends for dinner", initial_sensitive=False)
        self.store.append("assistant", "That usually makes the place feel alive.")
        hidden = self.store.append("user", "I never told anyone hosting makes me panic", initial_sensitive=False)
        self.store.append_privacy(hidden["id"], True, "sensor")
        self.store.append("user", "I fixed the loose shelf today", initial_sensitive=False)
        self.store.append("assistant", "Good, that is finally sorted.")
        self.store.append("user", "After last weekend I need more quiet than I thought", initial_sensitive=False)
        self.store.append("assistant", "Both reactions can matter.")

        handle_message("Should I host dinner again?", self.store, self.router)

        contents = [message["content"] for message in FakeRouter.request_body["messages"]]
        self.assertLess(contents.index("I love hosting friends for dinner"), contents.index("After last weekend I need more quiet than I thought"))
        self.assertNotIn(hidden["content"], contents)

    def test_router_fails_over_to_next_chat_model(self) -> None:
        FakeRouter.fail_models = {"first-model"}
        router = RouterClient(["first-model", "second-model"], self.base_url)

        self.assertEqual(router.chat("Hello Sage").reply, "Hello.")
        self.assertEqual(FakeRouter.seen_models, ["first-model", "second-model"])

    def test_router_fails_over_to_next_streaming_model(self) -> None:
        FakeRouter.fail_models = {"first-model"}
        router = RouterClient(["first-model", "second-model"], self.base_url)

        self.assertEqual(list(router.stream("Hello Sage")), ["Hel", "lo.", ""])
        self.assertEqual(FakeRouter.seen_models, ["first-model", "second-model"])

    def test_sensitive_terminal_never_reaches_router(self) -> None:
        reply = handle_message("I never told anyone about this confession", self.store, self.router)

        self.assertEqual(reply, SENSITIVE_ACKNOWLEDGEMENT)
        self.assertIsNone(FakeRouter.request_body)
        events = self.store.read_all()
        self.assertEqual([(event["role"], event["content"]) for event in events], [("user", "I never told anyone about this confession")])
        self.assertTrue(events[0]["sensitive"])

    def test_sensitive_carry_replays_after_restart(self) -> None:
        handle_message("I never told anyone about this confession", self.store, self.router)
        reopened = EventStore(self.store.data_root)

        reply = handle_message("ordinary follow-up", reopened, self.router)

        self.assertEqual(reply, SENSITIVE_ACKNOWLEDGEMENT)
        self.assertIsNone(FakeRouter.request_body)
        self.assertTrue(reopened.read_all()[-1]["sensitive"])

    def test_privacy_override_is_append_only(self) -> None:
        event = self.store.append("user", "ordinary message", initial_sensitive=False)
        before = self.store.path.read_text()

        self.assertTrue(self.store.set_sensitive(event["id"], True))

        self.assertTrue(self.store.path.read_text().startswith(before))
        self.assertTrue(self.store.read_all()[0]["sensitive"])
        self.assertTrue(self.store.set_sensitive(event["id"], False))
        self.assertFalse(self.store.read_all()[0]["sensitive"])

    def test_router_failure_keeps_user_event_without_assistant_event(self) -> None:
        FakeRouter.status = 503
        FakeRouter.response = {"error": "unavailable"}

        reply = handle_message("Hello Sage", self.store, self.router)

        self.assertEqual(reply, ROUTER_FAILURE)
        events = self.store.read_all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["role"], "user")

    def test_truncated_router_response_keeps_user_event_without_crashing(self) -> None:
        FakeRouter.truncate_response = True

        reply = handle_message("Hello Sage", self.store, self.router)

        self.assertEqual(reply, ROUTER_FAILURE)
        self.assertEqual([(event["role"], event["content"]) for event in self.store.read_all()], [("user", "Hello Sage")])

    def test_production_router_has_only_localhost_route(self) -> None:
        self.assertEqual(ROUTER_BASE_URL, "http://localhost:20128")
        self.assertEqual(RouterClient("free-tier-alias").endpoint, "http://localhost:20128/v1/chat/completions")

    def test_blank_alias_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RouterClient("   ")

    def test_search_stream_events_do_not_corrupt_the_response(self) -> None:
        class SearchingRouter:
            aliases = ("stub",)

            def chat_with_messages(self, messages, **kwargs):  # search decision
                return RouterResult(reply="sage project status")

            def stream_with_messages(self, messages, **kwargs):
                return iter(("answer", ""))

        web_server = SageServer(("127.0.0.1", 0), self.store, SearchingRouter())
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            payload = json.dumps({"message": "what shipped today"}).encode()
            request = Request(
                f"http://127.0.0.1:{web_server.server_port}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with patch("web.search", return_value=[]):
                with urlopen(request) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(
                        read_stream(response),
                        [
                            {"type": "search", "content": "sage project status"},
                            {"type": "search_error", "content": "Search returned no results"},
                            {"type": "delta", "content": "answer"},
                            {"type": "done"},
                        ],
                    )
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_browser_sensitive_never_reaches_router(self) -> None:
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            payload = json.dumps({"message": "I never told anyone about this confession"}).encode()
            request = Request(
                f"http://127.0.0.1:{web_server.server_port}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                self.assertEqual(
                    read_stream(response),
                    [{"type": "delta", "content": SENSITIVE_ACKNOWLEDGEMENT}, {"type": "done"}],
                )
                self.assertEqual(response.headers["X-Sage-Sensitive"], "true")
            self.assertIsNone(FakeRouter.request_body)
            events = self.store.read_all()
            self.assertEqual([(event["role"], event["content"]) for event in events], [("user", "I never told anyone about this confession")])
            self.assertTrue(events[0]["sensitive"])
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_browser_ephemeral_mode_holds_message_before_provider_work(self) -> None:
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            payload = json.dumps({"message": "ordinary private note", "sensitive_mode": True}).encode()
            request = Request(
                f"http://127.0.0.1:{web_server.server_port}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                self.assertEqual(
                    read_stream(response),
                    [{"type": "delta", "content": SENSITIVE_ACKNOWLEDGEMENT}, {"type": "done"}],
                )
                self.assertEqual(response.headers["X-Sage-Sensitive"], "true")
            self.assertIsNone(FakeRouter.request_body)
            event = self.store.read_all()[0]
            self.assertTrue(event["sensitive"])
            self.assertTrue(event["provider_excluded"])
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_browser_rejects_invalid_ephemeral_mode(self) -> None:
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            payload = json.dumps({"message": "Hello Sage", "sensitive_mode": "yes"}).encode()
            request = Request(
                f"http://127.0.0.1:{web_server.server_port}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(self.store.read_all(), [])
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_browser_privacy_override(self) -> None:
        event = self.store.append("user", "ordinary message", initial_sensitive=False)
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            payload = json.dumps({"sensitive": True}).encode()
            request = Request(
                f"http://127.0.0.1:{web_server.server_port}/api/events/{event['id']}/privacy",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                self.assertEqual(json.load(response)["sensitive"], True)
            self.assertTrue(self.store.read_all()[0]["sensitive"])
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_browser_history_and_completed_stream_persist_events(self) -> None:
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            base_url = f"http://127.0.0.1:{web_server.server_port}"
            with urlopen(f"{base_url}/") as response:
                self.assertIn(b"Message Sage", response.read())
            with urlopen(f"{base_url}/static/app.css") as response:
                self.assertIn(b"-webkit-tap-highlight-color: transparent", response.read())
            payload = json.dumps({"message": "Hello Sage"}).encode()
            request = Request(f"{base_url}/api/chat", data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request) as response:
                self.assertEqual(
                    read_stream(response),
                    [{"type": "delta", "content": "Hel"}, {"type": "delta", "content": "lo."}, {"type": "done"}],
                )
            with urlopen(f"{base_url}/api/history") as response:
                events = json.load(response)["events"]
            self.assertEqual([(event["role"], event["content"]) for event in events], [("user", "Hello Sage"), ("assistant", "Hello.")])
            self.assertEqual(FakeRouter.request_body["stream"], True)
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_browser_clear_chat_preserves_events_and_resets_visible_history(self) -> None:
        self.store.append("user", "Old visible chat", initial_sensitive=False)
        self.store.append("assistant", "Still remembered")
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            base_url = f"http://127.0.0.1:{web_server.server_port}"
            request = Request(f"{base_url}/api/chat/clear", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request) as response:
                self.assertEqual(json.load(response), {"ok": True})
            with urlopen(f"{base_url}/api/history") as response:
                self.assertEqual(json.load(response)["events"], [])
            self.assertEqual(
                [(event["role"], event["content"]) for event in EventStore(self.data_root).read_all()],
                [("user", "Old visible chat"), ("assistant", "Still remembered")],
            )
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_read_ignores_incomplete_final_record(self) -> None:
        self.store.path.write_text('{"role":"user","content":"saved","said_at":"2026-08-15T00:00:00Z"}\n{"role"')

        self.assertEqual([(event["role"], event["content"]) for event in self.store.read_all()], [("user", "saved")])

    def test_legacy_held_close_records_are_read_as_sensitive(self) -> None:
        self.store.path.write_text(
            '{"id":"old","role":"user","content":"legacy secret","said_at":"2026-08-15T00:00:00Z","held_close":true,"provider_excluded":true}\n'
            '{"kind":"privacy","target_id":"old","held_close":true,"source":"user","said_at":"2026-08-15T00:00:01Z"}\n'
        )

        event = self.store.read_all()[0]
        self.assertTrue(event["sensitive"])
        self.assertTrue(event["provider_excluded"])
        self.assertEqual(self.store.recall("legacy secret"), [])

    def test_unclassified_user_event_is_fail_closed_for_provider_work(self) -> None:
        event = self.store.append("user", "possibly private", initial_sensitive=None)

        self.assertTrue(event["provider_excluded"])
        self.assertEqual(self.store.recall("private"), [])
        self.assertEqual(self.store.heartbeat_completed("entities"), set())

    def test_initial_privacy_classification_is_stored_with_event(self) -> None:
        event = self.store.append("user", "sensitive from providers", initial_sensitive=True)
        reopened = EventStore(self.store.data_root)

        self.assertTrue(event["sensitive"])
        self.assertTrue(event["provider_excluded"])
        self.assertTrue(reopened.read_all()[0]["sensitive"])
        self.assertTrue(reopened.read_all()[0]["provider_excluded"])

    def test_privacy_metadata_failure_keeps_classified_event_safe(self) -> None:
        store = FailingPrivacyStore(self.data_root)

        reply = handle_message("I never told anyone about this", store, self.router)

        self.assertEqual(reply, "I'm holding this close.")
        self.assertTrue(store.read_all()[0]["sensitive"])
        self.assertIsNone(FakeRouter.request_body)

    def test_health_does_not_disclose_model_alias(self) -> None:
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{web_server.server_port}/health") as response:
                self.assertEqual(json.load(response), {"ok": True})
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_heartbeat_excludes_unclassified_and_sensitive_events_and_deduplicates(self) -> None:
        private = self.store.append("user", "secret event", initial_sensitive=True)
        unclassified = self.store.append("user", "maybe private", initial_sensitive=None)
        public = self.store.append("user", "router project update", initial_sensitive=False)
        self.store.append("assistant", "Understood")
        scribe = FakeScribe()
        heartbeat = Heartbeat(self.store, self.interior, scribe)

        heartbeat.beat()
        first_body = json.dumps(scribe.messages)
        heartbeat.beat()

        self.assertNotIn(private["content"], first_body)
        self.assertNotIn(unclassified["content"], first_body)
        self.assertIn(public["content"], first_body)
        self.assertEqual(len(self.store.heartbeat_completed("entities")), 2)
        self.assertEqual(len(self.interior.list_reflections()), 1)
        self.assertEqual(len(self.store.heartbeat_completed("reflection")), 1)

    def test_browser_rejects_untrusted_origin_and_host(self) -> None:
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            url = f"http://127.0.0.1:{web_server.server_port}/api/chat"
            payload = json.dumps({"message": "Hello Sage"}).encode()
            request = Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 403)
            request = Request(url, headers={"Host": "evil.example"})
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 403)
            self.assertEqual(self.store.read_all(), [])
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_browser_accepts_numeric_network_host_with_same_origin(self) -> None:
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            host = f"192.168.1.20:{web_server.server_port}"
            request = Request(
                f"http://127.0.0.1:{web_server.server_port}/api/chat",
                data=json.dumps({"message": "Hello Sage"}).encode(),
                headers={"Content-Type": "application/json", "Host": host, "Origin": f"http://{host}"},
                method="POST",
            )
            with urlopen(request) as response:
                self.assertEqual(read_stream(response)[-1], {"type": "done"})
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_browser_rejects_missing_or_oversized_body(self) -> None:
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            url = f"http://127.0.0.1:{web_server.server_port}/api/chat"
            request = Request(url, data=b"", headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 413)
            request = Request(url, data=b"x" * (64 * 1024 + 1), headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 413)
            self.assertEqual(self.store.read_all(), [])
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_browser_incomplete_stream_persists_user_only(self) -> None:
        FakeRouter.truncate_stream = True
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            payload = json.dumps({"message": "Hello Sage"}).encode()
            request = Request(
                f"http://127.0.0.1:{web_server.server_port}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                events = read_stream(response)
                self.assertEqual(events[-1], {"type": "error", "content": ROUTER_FAILURE})
            self.assertEqual([(event["role"], event["content"]) for event in self.store.read_all()], [("user", "Hello Sage")])
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_browser_stream_uses_http11_without_exposing_chunk_markers(self) -> None:
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{web_server.server_port}/api/chat",
                data=json.dumps({"message": "Hello Sage"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                self.assertEqual(response.version, 11)
                self.assertEqual(response.headers["Content-Type"], "application/x-ndjson; charset=utf-8")
                self.assertEqual(read_stream(response)[-1], {"type": "done"})
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_browser_stream_emits_first_delta_before_model_completes(self) -> None:
        FakeRouter.stream_gate = ThreadEvent()
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        response = None
        try:
            request = Request(
                f"http://127.0.0.1:{web_server.server_port}/api/chat",
                data=json.dumps({"message": "Hello Sage"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            response = urlopen(request)
            self.assertEqual(json.loads(response.readline()), {"type": "delta", "content": "Hel"})
            self.assertEqual([(event["role"], event["content"]) for event in self.store.read_all()], [("user", "Hello Sage")])
            FakeRouter.stream_gate.set()
            self.assertEqual(read_stream(response)[-1], {"type": "done"})
        finally:
            FakeRouter.stream_gate.set()
            if response is not None:
                response.close()
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_interior_reflections_and_beliefs_persistence(self) -> None:
        ref = self.interior.append_reflection("I noticed Elliot's focus on rhythm.")
        self.assertEqual(len(self.interior.list_reflections()), 1)
        self.assertEqual(self.interior.list_reflections()[0]["content"], "I noticed Elliot's focus on rhythm.")

        self.interior.beliefs_path.parent.mkdir(parents=True, exist_ok=True)
        self.interior.beliefs_path.write_text(
            json.dumps({
                "id": "belief-1",
                "topic": "free-tier routing",
                "stance": "essential invariant",
                "evidence": "keeps Sage local and sustainable",
                "said_at": "2026-08-25T00:00:00Z",
            }) + "\n"
        )
        self.assertEqual(len(self.interior.list_beliefs()), 1)
        self.assertEqual(self.interior.list_beliefs()[0]["topic"], "free-tier routing")
        self.assertFalse(hasattr(self.interior, "append_belief"))

    def test_waiting_message_prepended_and_cleared_on_chat(self) -> None:
        self.interior.set_waiting_message("Hey Elliot, did that deploy succeed?")
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router, self.interior)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            base_url = f"http://127.0.0.1:{web_server.server_port}"
            with urlopen(f"{base_url}/api/history") as response:
                events = json.load(response)["events"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["kind"], "waiting")
            self.assertEqual(events[0]["content"], "Hey Elliot, did that deploy succeed?")

            # Sending a message acknowledges and clears the waiting message
            payload = json.dumps({"message": "Yes, it did."}).encode()
            request = Request(f"{base_url}/api/chat", data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request) as response:
                self.assertEqual(read_stream(response)[-1], {"type": "done"})

            self.assertIsNone(self.interior.get_waiting_message())
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_entity_observation_persistence(self) -> None:
        self.store.append_entity_observation("qwen", "Qwen 3.8 Max", "primary free-tier chat model")
        observations = self.store.entity_observations()
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["entity_id"], "qwen")
        self.assertEqual(observations[0]["name"], "Qwen 3.8 Max")

    def test_heartbeat_splits_reflection_and_extraction_routers(self) -> None:
        self.store.append("user", "Working on the pressure model", initial_sensitive=False)
        self.store.append("assistant", "Noted")
        chat = FakeScribe()
        extract = FakeScribe()
        heartbeat = Heartbeat(self.store, self.interior, chat, extract_router=extract)

        heartbeat.beat()

        extraction_prompts = [m[0]["content"] for m in extract.messages]
        reflection_prompts = [m[0]["content"] for m in chat.messages]
        self.assertTrue(extraction_prompts)
        self.assertTrue(all(p.startswith("Extract key durable entities") for p in extraction_prompts))
        self.assertTrue(any("reflecting privately" in p for p in reflection_prompts))
        self.assertFalse(any(p.startswith("Extract key durable entities") for p in reflection_prompts))
        self.assertEqual(heartbeat.failure_counts.get("reflection"), 0)

    def test_heartbeat_counts_background_failures_instead_of_swallowing_them(self) -> None:
        self.store.append("user", "Something worth reflecting on", initial_sensitive=False)
        self.store.append("assistant", "Noted")
        heartbeat = Heartbeat(self.store, self.interior, DeadRouter())

        with self.assertLogs("sage.heartbeat", level="ERROR"):
            heartbeat.beat()
            heartbeat.beat()

        self.assertEqual(heartbeat.failure_counts["reflection"], 2)
        self.assertGreaterEqual(heartbeat.failure_counts["entity extraction"], 2)
        self.assertEqual(self.interior.list_reflections(), [])

    def test_reflection_marker_sets_self_category(self) -> None:
        self.assertEqual(
            parse_reflection("SELF: I reused the opener I promised to drop."),
            ("self", "I reused the opener I promised to drop."),
        )
        # Bold, lowercase and a missing space are all drift a small model actually produces.
        self.assertEqual(parse_reflection('  **self:**I reused it.  '), ("self", "I reused it."))
        self.assertEqual(parse_reflection("SELF:\nI reused it."), ("self", "I reused it."))

    def test_unmarked_reflection_stays_general(self) -> None:
        plain = "Elliot moved from a greeting to existential doubt in three beats."
        self.assertEqual(parse_reflection(plain), ("general", plain))
        # A quoted marker inside the reflection must never mint a self-claim.
        quoted = 'He pasted "SELF: I always hedge" back at me from the notebook.'
        self.assertEqual(parse_reflection(quoted), ("general", quoted))

    def test_stray_label_is_stripped_without_marking_self(self) -> None:
        self.assertEqual(parse_reflection("CONTEXT: The rhythm reset."), ("general", "The rhythm reset."))

    def test_bare_marker_stores_nothing_and_stays_retryable(self) -> None:
        self.assertEqual(parse_reflection("SELF:"), ("self", ""))
        self.store.append("user", "Something worth reflecting on", initial_sensitive=False)
        self.store.append("assistant", "Noted")
        heartbeat = Heartbeat(self.store, self.interior, FakeScribe("SELF:"))

        heartbeat.beat()

        self.assertEqual(self.interior.list_reflections(), [])
        self.assertEqual(self.store.heartbeat_completed("reflection"), set())

    def test_heartbeat_stores_self_category_with_marker_stripped(self) -> None:
        self.store.append("user", "You said you would drop that opener", initial_sensitive=False)
        self.store.append("assistant", "Noted")
        heartbeat = Heartbeat(self.store, self.interior, FakeScribe("SELF: I used the opener again."))

        heartbeat.beat()

        reflections = self.interior.list_reflections()
        self.assertEqual(len(reflections), 1)
        self.assertEqual(reflections[0]["category"], "self")
        self.assertEqual(reflections[0]["content"], "I used the opener again.")

    def test_identity_ruling_folds_last_verdict_over_intact_proposal(self) -> None:
        proposal = self.interior.append_identity_proposal(
            "I restate an intention and then contradict it inside the same exchange",
            evidence=["reflection-1", "reflection-2"],
        )
        self.assertEqual([entry["status"] for entry in self.interior.list_identity()], ["proposed"])

        self.interior.append_identity_ruling(proposal["id"], "ratified")
        self.interior.append_identity_ruling(proposal["id"], "retired")

        entries = self.interior.list_identity()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["status"], "retired")
        self.assertEqual(entries[0]["evidence"], ["reflection-1", "reflection-2"])
        # Rulings are appended; the proposal line itself is never rewritten.
        lines = self.interior.identity_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(json.loads(lines[0]), proposal)

    def test_identity_ruling_for_unknown_proposal_is_ignored(self) -> None:
        self.interior.append_identity_ruling("no-such-proposal", "ratified")
        self.assertEqual(self.interior.list_identity(), [])

    def test_identity_write_rejects_blank_claim_and_unknown_verdict(self) -> None:
        with self.assertRaises(ValueError):
            self.interior.append_identity_proposal("   ", evidence=["reflection-1"])
        proposal = self.interior.append_identity_proposal("I hedge before answering", evidence=[])
        with self.assertRaises(ValueError):
            self.interior.append_identity_ruling(proposal["id"], "approved")

    def test_no_identity_proposal_without_a_self_observation(self) -> None:
        self.store.append("user", "Working on the pressure model", initial_sensitive=False)
        self.store.append("assistant", "Noted")
        scribe = FakeScribe("Elliot is testing the edges of his own reality.")
        heartbeat = Heartbeat(self.store, self.interior, scribe)

        heartbeat.beat()

        self.assertEqual(self.interior.list_identity(), [])
        # Resting state costs no model call of its own.
        self.assertFalse(any("claim about yourself" in m[0]["content"] for m in scribe.messages))

    def test_self_observation_becomes_a_proposal_awaiting_ratification(self) -> None:
        self.store.append("user", "You said you would drop that opener", initial_sensitive=False)
        self.store.append("assistant", "Noted")
        scribe = FakeScribe("SELF: I used the opener again.")
        heartbeat = Heartbeat(self.store, self.interior, scribe)

        heartbeat.beat()

        reflection = self.interior.list_reflections()[0]
        entries = self.interior.list_identity()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["claim"], "I used the opener again.")
        self.assertEqual(entries[0]["evidence"], [reflection["id"]])
        self.assertEqual(entries[0]["status"], "proposed")

    def test_an_already_proposed_observation_is_not_proposed_again(self) -> None:
        self.store.append("user", "You said you would drop that opener", initial_sensitive=False)
        self.store.append("assistant", "Noted")
        scribe = FakeScribe("SELF: I used the opener again.")
        heartbeat = Heartbeat(self.store, self.interior, scribe)

        heartbeat.beat()
        heartbeat.beat()

        self.assertEqual(len(self.interior.list_identity()), 1)

    def test_compose_identity_block_empty_when_no_ratified(self) -> None:
        """No ratified entries means empty block."""
        from sage import compose_identity_block
        self.assertEqual(compose_identity_block(self.interior), "")
        # A proposal alone is not enough
        self.interior.append_identity_proposal("I am kind", ["r1"])
        self.assertEqual(compose_identity_block(self.interior), "")

    def test_compose_identity_block_includes_ratified_claims(self) -> None:
        from sage import compose_identity_block
        p1 = self.interior.append_identity_proposal("I tend to over-explain", ["r1"])
        self.interior.append_identity_ruling(p1["id"], "ratified")
        block = compose_identity_block(self.interior)
        self.assertIn("Things I have noticed about myself", block)
        self.assertIn("- I tend to over-explain", block)

    def test_compose_identity_block_caps_at_ten_newest(self) -> None:
        from sage import compose_identity_block
        ids = []
        for i in range(15):
            p = self.interior.append_identity_proposal(f"Trait {i}", [f"r{i}"])
            self.interior.append_identity_ruling(p["id"], "ratified")
            ids.append(p["id"])
        block = compose_identity_block(self.interior)
        # Should have exactly 10 entries
        self.assertEqual(block.count("- Trait"), 10)
        # Newest (Trait 14) should appear, oldest (Trait 0) should not
        self.assertIn("Trait 14", block)
        self.assertNotIn("Trait 0", block)

    def test_load_directive_appends_identity_block(self) -> None:
        from sage import load_directive
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("I am Sage.")
            tmp = f.name
        try:
            result = load_directive(Path(tmp), identity_block="\n\n---\nExtra")
            self.assertEqual(result, "I am Sage.\n\n---\nExtra")
            # Without identity_block, original behavior
            result2 = load_directive(Path(tmp))
            self.assertEqual(result2, "I am Sage.")
        finally:
            Path(tmp).unlink()

    def test_compose_identity_block_failsoft_on_broken_interior(self) -> None:
        """If interior.list_identity() raises, compose returns empty string."""
        from sage import compose_identity_block
        class BrokenInterior:
            def list_identity(self):
                raise RuntimeError("disk on fire")
        self.assertEqual(compose_identity_block(BrokenInterior()), "")

    def test_api_identity_returns_empty_list(self) -> None:
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router, self.interior)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            base_url = f"http://127.0.0.1:{web_server.server_port}"
            with urlopen(f"{base_url}/api/identity") as response:
                data = json.load(response)
            self.assertEqual(data, {"identity": []})
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_api_identity_returns_proposals_with_status(self) -> None:
        p = self.interior.append_identity_proposal("I over-explain", ["r1"])
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router, self.interior)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            base_url = f"http://127.0.0.1:{web_server.server_port}"
            with urlopen(f"{base_url}/api/identity") as response:
                data = json.load(response)
            entries = data["identity"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["claim"], "I over-explain")
            self.assertEqual(entries[0]["status"], "proposed")
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_api_ratify_identity_entry(self) -> None:
        p = self.interior.append_identity_proposal("I over-explain", ["r1"])
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router, self.interior)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            base_url = f"http://127.0.0.1:{web_server.server_port}"
            req = Request(f"{base_url}/api/identity/{p['id']}/ratify", method="POST", data=b"")
            with urlopen(req) as response:
                result = json.load(response)
            self.assertEqual(result["verdict"], "ratified")
            # Verify it took effect
            with urlopen(f"{base_url}/api/identity") as response:
                entries = json.load(response)["identity"]
            self.assertEqual(entries[0]["status"], "ratified")
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_api_reject_identity_entry(self) -> None:
        p = self.interior.append_identity_proposal("Bad trait", ["r1"])
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router, self.interior)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            base_url = f"http://127.0.0.1:{web_server.server_port}"
            req = Request(f"{base_url}/api/identity/{p['id']}/reject", method="POST", data=b"")
            with urlopen(req) as response:
                result = json.load(response)
            self.assertEqual(result["verdict"], "rejected")
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_api_identity_ruling_unknown_id_returns_404(self) -> None:
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router, self.interior)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            base_url = f"http://127.0.0.1:{web_server.server_port}"
            req = Request(f"{base_url}/api/identity/bogus-id/ratify", method="POST", data=b"")
            with self.assertRaises(HTTPError) as ctx:
                urlopen(req)
            self.assertEqual(ctx.exception.code, 404)
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_metabolism_completion_tracking(self) -> None:
        self.store.append("user", "Hello")
        event = self.store.read_all()[0]
        self.store.append_heartbeat_completion("metabolism", event["id"])
        completed = self.store.heartbeat_completed("metabolism")
        self.assertIn(event["id"], completed)

    def test_gap_scan_returns_empty_on_no_gaps(self) -> None:
        from metabolism import gap_scan
        scribe = FakeScribe("[]")
        result = gap_scan(
            [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}],
            scribe,
            self.interior,
            "evt-1",
        )
        self.assertEqual(result, [])
        self.assertFalse(self.interior.metabolism_path.exists())

    def test_gap_scan_returns_gaps_and_writes_record(self) -> None:
        from metabolism import gap_scan
        import json as _json
        scribe = FakeScribe('[{"gap": "What is WIB timezone offset?", "query": "WIB timezone UTC offset"}]')
        result = gap_scan(
            [{"role": "user", "content": "What time is it in WIB?"}, {"role": "assistant", "content": "I am not sure of the exact offset."}],
            scribe,
            self.interior,
            "evt-2",
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["gap"], "What is WIB timezone offset?")
        self.assertTrue(self.interior.metabolism_path.exists())
        records = [_json.loads(line) for line in self.interior.metabolism_path.read_text().splitlines()]
        self.assertEqual(records[0]["kind"], "gap_scan")
        self.assertEqual(records[0]["source_event_id"], "evt-2")

    def test_gap_scan_returns_empty_on_router_failure(self) -> None:
        from metabolism import gap_scan
        result = gap_scan(
            [{"role": "user", "content": "Hi"}],
            DeadRouter(),
            self.interior,
            "evt-3",
        )
        self.assertEqual(result, [])

    def test_explore_searches_gaps_and_stores_events(self) -> None:
        from metabolism import explore
        from unittest.mock import patch
        from search import SearchResult
        fake_results = [SearchResult(title="WIB", snippet="UTC+7", url="https://example.com")]
        with patch("metabolism.search", return_value=fake_results):
            result = explore(
                [{"gap": "WIB offset", "query": "WIB timezone"}],
                self.store,
                self.interior,
                "evt-1",
            )
        self.assertEqual(len(result), 1)
        self.assertIn("results", result[0])
        events = self.store.read_all()
        metabolism_events = [e for e in events if "[Metabolism search:" in e["content"]]
        self.assertEqual(len(metabolism_events), 1)

    def test_explore_returns_empty_when_all_searches_fail(self) -> None:
        from metabolism import explore
        from unittest.mock import patch
        with patch("metabolism.search", return_value=[]):
            result = explore(
                [{"gap": "Unknown thing", "query": "unknown query"}],
                self.store,
                self.interior,
                "evt-2",
            )
        self.assertEqual(result, [])

    def test_digest_creates_metabolism_reflection(self) -> None:
        from metabolism import digest
        scribe = FakeScribe("I learned that WIB is UTC+7, which connects to the scheduling question.")
        result = digest(
            [{"gap": "WIB offset", "query": "WIB timezone", "results": [{"title": "WIB", "snippet": "UTC+7", "url": "https://example.com"}]}],
            scribe,
            self.interior,
            "evt-1",
        )
        self.assertIsNotNone(result)
        reflections = self.interior.list_reflections(limit=100)
        metabolism_refs = [r for r in reflections if r.get("category") == "metabolism"]
        self.assertEqual(len(metabolism_refs), 1)
        self.assertEqual(metabolism_refs[0]["source_event_id"], "evt-1")

    def test_digest_returns_none_on_router_failure(self) -> None:
        from metabolism import digest
        result = digest(
            [{"gap": "test", "query": "test", "results": [{"title": "t", "snippet": "s", "url": "u"}]}],
            DeadRouter(),
            self.interior,
            "evt-2",
        )
        self.assertIsNone(result)

if __name__ == "__main__":
    unittest.main()
