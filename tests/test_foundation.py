from __future__ import annotations

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import EventStore
from interior import InteriorStore
from router import ROUTER_BASE_URL, RouterClient, RouterResult
from sage import HELD_CLOSE_ACKNOWLEDGEMENT, ROUTER_FAILURE, build_router_messages, handle_message, load_directive
from heartbeat import Heartbeat
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
            for chunk in type(self).stream_chunks:
                self.wfile.write(f'data: {{"choices":[{{"delta":{{"content":{json.dumps(chunk)}}}}}]}}\n\n'.encode())
                self.wfile.flush()
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
    def __init__(self) -> None:
        self.messages: list[list[dict[str, str]]] = []

    def chat_with_messages(self, messages: list[dict[str, str]]) -> RouterResult:
        self.messages.append(messages)
        if messages[0]["content"].startswith("Extract key durable entities"):
            return RouterResult("[]")
        return RouterResult("A reflection about the current conversation.")


class FailingPrivacyStore(EventStore):
    def append_privacy(self, *args: object, **kwargs: object) -> object:
        raise OSError("privacy metadata unavailable")


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
        self.store.append("user", "Noisy weather update", initial_held_close=False)
        self.store.append("assistant", "Sunny tomorrow")
        self.store.append("user", "I never told anyone about this confession", initial_held_close=False)

        self.store.append("assistant", "Thanks for sharing")
        self.store.append("user", "I need advice", initial_held_close=False)

        store = EventStore(self.store.data_root)
        self.assertEqual(
            [(event["role"], event["content"]) for event in store.recall("advice")],
            [("user", "I need advice")],
        )

    def test_recall_prefers_exact_match_over_keyword_ties(self) -> None:
        self.store.append("user", "Need a cup of tea and a sandwich", initial_held_close=False)
        self.store.append("user", "Need help with the tea recipe", initial_held_close=False)

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
        self.store.append("user", "We bought apples and oranges for lunch", initial_held_close=False)
        self.store.append("user", "Apples are great, I love green apples and red apples", initial_held_close=False)
        self.store.append("user", "Just talking about oranges", initial_held_close=False)

        recalled = self.store.recall("apples apples", limit=2)
        self.assertEqual(len(recalled), 2)
        self.assertEqual(recalled[0]["content"], "Apples are great, I love green apples and red apples")
        self.assertEqual(recalled[1]["content"], "We bought apples and oranges for lunch")

    def test_recall_treats_stopword_only_query_as_context_fallback(self) -> None:
        self.store.append("user", "First topic", initial_held_close=False)
        self.store.append("assistant", "Answer")

        self.assertEqual(
            [(event["role"], event["content"]) for event in self.store.recall("the and a")],
            [("user", "First topic"), ("assistant", "Answer")],
        )

    def test_recall_excludes_held_close_events(self) -> None:
        self.store.append("user", "open topic", initial_held_close=False)
        hidden = self.store.append("user", "I never told anyone about this", initial_held_close=False)
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
        self.store.append("user", "I keep buying potatoes even when I plan to cook something else", initial_held_close=False)
        self.store.append("assistant", "You seem to like having them around.")

        messages = build_router_messages("I made them again tonight", self.store, directive=load_directive())

        self.assertEqual(messages[0], {"role": "system", "content": load_directive()})
        self.assertIn({"role": "user", "content": "I keep buying potatoes even when I plan to cook something else"}, messages)
        self.assertEqual(messages[-1], {"role": "user", "content": "I made them again tonight"})

    def test_prompt_context_keeps_recent_turns_and_adds_older_recall(self) -> None:
        self.store.append("user", "I want to learn pottery this year", initial_held_close=False)
        self.store.append("assistant", "That sounds like a good creative outlet.")
        self.store.append("user", "Recent one", initial_held_close=False)
        self.store.append("assistant", "Recent answer one")
        self.store.append("user", "Recent two", initial_held_close=False)
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

    def test_chat_sends_contradictory_public_history_without_held_close_match(self) -> None:
        self.store.append("user", "I love hosting friends for dinner", initial_held_close=False)
        self.store.append("assistant", "That usually makes the place feel alive.")
        hidden = self.store.append("user", "I never told anyone hosting makes me panic", initial_held_close=False)
        self.store.append_privacy(hidden["id"], True, "sensor")
        self.store.append("user", "I fixed the loose shelf today", initial_held_close=False)
        self.store.append("assistant", "Good, that is finally sorted.")
        self.store.append("user", "After last weekend I need more quiet than I thought", initial_held_close=False)
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

    def test_held_close_terminal_never_reaches_router(self) -> None:
        reply = handle_message("I never told anyone about this confession", self.store, self.router)

        self.assertEqual(reply, HELD_CLOSE_ACKNOWLEDGEMENT)
        self.assertIsNone(FakeRouter.request_body)
        events = self.store.read_all()
        self.assertEqual([(event["role"], event["content"]) for event in events], [("user", "I never told anyone about this confession")])
        self.assertTrue(events[0]["held_close"])

    def test_held_close_carry_replays_after_restart(self) -> None:
        handle_message("I never told anyone about this confession", self.store, self.router)
        reopened = EventStore(self.store.data_root)

        reply = handle_message("ordinary follow-up", reopened, self.router)

        self.assertEqual(reply, HELD_CLOSE_ACKNOWLEDGEMENT)
        self.assertIsNone(FakeRouter.request_body)
        self.assertTrue(reopened.read_all()[-1]["held_close"])

    def test_privacy_override_is_append_only(self) -> None:
        event = self.store.append("user", "ordinary message", initial_held_close=False)
        before = self.store.path.read_text()

        self.assertTrue(self.store.set_held_close(event["id"], True))

        self.assertTrue(self.store.path.read_text().startswith(before))
        self.assertTrue(self.store.read_all()[0]["held_close"])
        self.assertTrue(self.store.set_held_close(event["id"], False))
        self.assertFalse(self.store.read_all()[0]["held_close"])

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

    def test_browser_held_close_never_reaches_router(self) -> None:
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
                self.assertEqual(response.read().decode(), HELD_CLOSE_ACKNOWLEDGEMENT)
                self.assertEqual(response.headers["X-Sage-Held-Close"], "true")
            self.assertIsNone(FakeRouter.request_body)
            events = self.store.read_all()
            self.assertEqual([(event["role"], event["content"]) for event in events], [("user", "I never told anyone about this confession")])
            self.assertTrue(events[0]["held_close"])
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_browser_ephemeral_mode_holds_message_before_provider_work(self) -> None:
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            payload = json.dumps({"message": "ordinary private note", "held_close_mode": True}).encode()
            request = Request(
                f"http://127.0.0.1:{web_server.server_port}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                self.assertEqual(response.read().decode(), HELD_CLOSE_ACKNOWLEDGEMENT)
                self.assertEqual(response.headers["X-Sage-Held-Close"], "true")
            self.assertIsNone(FakeRouter.request_body)
            event = self.store.read_all()[0]
            self.assertTrue(event["held_close"])
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
            payload = json.dumps({"message": "Hello Sage", "held_close_mode": "yes"}).encode()
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
        event = self.store.append("user", "ordinary message", initial_held_close=False)
        web_server = SageServer(("127.0.0.1", 0), self.store, self.router)
        web_thread = Thread(target=web_server.serve_forever)
        web_thread.start()
        try:
            payload = json.dumps({"held_close": True}).encode()
            request = Request(
                f"http://127.0.0.1:{web_server.server_port}/api/events/{event['id']}/privacy",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                self.assertEqual(json.load(response)["held_close"], True)
            self.assertTrue(self.store.read_all()[0]["held_close"])
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
            payload = json.dumps({"message": "Hello Sage"}).encode()
            request = Request(f"{base_url}/api/chat", data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request) as response:
                self.assertEqual(response.read().decode(), "Hello.")
            with urlopen(f"{base_url}/api/history") as response:
                events = json.load(response)["events"]
            self.assertEqual([(event["role"], event["content"]) for event in events], [("user", "Hello Sage"), ("assistant", "Hello.")])
            self.assertEqual(FakeRouter.request_body["stream"], True)
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_read_ignores_incomplete_final_record(self) -> None:
        self.store.path.write_text('{"role":"user","content":"saved","said_at":"2026-08-15T00:00:00Z"}\n{"role"')

        self.assertEqual([(event["role"], event["content"]) for event in self.store.read_all()], [("user", "saved")])

    def test_unclassified_user_event_is_fail_closed_for_provider_work(self) -> None:
        event = self.store.append("user", "possibly private", initial_held_close=None)

        self.assertTrue(event["provider_excluded"])
        self.assertEqual(self.store.recall("private"), [])
        self.assertEqual(self.store.heartbeat_completed("entities"), set())

    def test_initial_privacy_classification_is_stored_with_event(self) -> None:
        event = self.store.append("user", "held from providers", initial_held_close=True)
        reopened = EventStore(self.store.data_root)

        self.assertTrue(event["held_close"])
        self.assertTrue(event["provider_excluded"])
        self.assertTrue(reopened.read_all()[0]["held_close"])
        self.assertTrue(reopened.read_all()[0]["provider_excluded"])

    def test_privacy_metadata_failure_keeps_classified_event_safe(self) -> None:
        store = FailingPrivacyStore(self.data_root)

        reply = handle_message("I never told anyone about this", store, self.router)

        self.assertEqual(reply, "I'm holding this close.")
        self.assertTrue(store.read_all()[0]["held_close"])
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

    def test_heartbeat_excludes_unclassified_and_held_events_and_deduplicates(self) -> None:
        private = self.store.append("user", "secret event", initial_held_close=True)
        unclassified = self.store.append("user", "maybe private", initial_held_close=None)
        public = self.store.append("user", "Mimo project update", initial_held_close=False)
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
                self.assertIn(ROUTER_FAILURE, response.read().decode())
            self.assertEqual([(event["role"], event["content"]) for event in self.store.read_all()], [("user", "Hello Sage")])
        finally:
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
                self.assertEqual(response.read().decode(), "Hello.")

            self.assertIsNone(self.interior.get_waiting_message())
        finally:
            web_server.shutdown()
            web_thread.join()
            web_server.server_close()

    def test_entity_observation_persistence(self) -> None:
        obs = self.store.append_entity_observation("mimo", "Mimo v2.5", "primary free-tier chat model")
        observations = self.store.entity_observations()
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["entity_id"], "mimo")
        self.assertEqual(observations[0]["name"], "Mimo v2.5")


if __name__ == "__main__":
    unittest.main()
