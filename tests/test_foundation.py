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
from router import ROUTER_BASE_URL, RouterClient
from sage import ROUTER_FAILURE, handle_message
from web import SageServer


class FakeRouter(BaseHTTPRequestHandler):
    status = 200
    response: object = {"choices": [{"message": {"content": "Hello."}}]}
    request_body: dict[str, object] | None = None
    truncate_response = False
    stream_chunks = ["Hel", "lo."]
    truncate_stream = False

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        type(self).request_body = json.loads(self.rfile.read(length))
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


class FoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeRouter.status = 200
        FakeRouter.response = {"choices": [{"message": {"content": "Hello."}}]}
        FakeRouter.request_body = None
        FakeRouter.truncate_response = False
        FakeRouter.stream_chunks = ["Hel", "lo."]
        FakeRouter.truncate_stream = False
        self.server = ThreadingHTTPServer(("localhost", 0), FakeRouter)
        self.thread = Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base_url = f"http://localhost:{self.server.server_port}"
        self.temporary_directory = TemporaryDirectory()
        self.store = EventStore(Path(self.temporary_directory.name))
        self.router = RouterClient("free-tier-alias", self.base_url)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temporary_directory.cleanup()

    def test_success_persists_separate_utc_events_and_routes_alias(self) -> None:
        reply = handle_message("Hello Sage", self.store, self.router)

        self.assertEqual(reply, "Hello.")
        self.assertEqual(
            FakeRouter.request_body,
            {
                "model": "free-tier-alias",
                "messages": [{"role": "user", "content": "Hello Sage"}],
            },
        )
        events = EventStore(self.store.data_root).read_all()
        self.assertEqual([(event["role"], event["content"]) for event in events], [("user", "Hello Sage"), ("assistant", "Hello.")])
        for event in events:
            self.assertEqual(datetime.fromisoformat(event["said_at"].replace("Z", "+00:00")).utcoffset().total_seconds(), 0)

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


if __name__ == "__main__":
    unittest.main()
