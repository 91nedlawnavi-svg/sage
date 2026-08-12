from __future__ import annotations

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import EventStore
from router import ROUTER_BASE_URL, RouterClient
from sage import ROUTER_FAILURE, handle_message


class FakeRouter(BaseHTTPRequestHandler):
    status = 200
    response: object = {"choices": [{"message": {"content": "Hello."}}]}
    request_body: dict[str, object] | None = None

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        type(self).request_body = json.loads(self.rfile.read(length))
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(type(self).response).encode())

    def log_message(self, format: str, *args: object) -> None:
        return


class FoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeRouter.status = 200
        FakeRouter.response = {"choices": [{"message": {"content": "Hello."}}]}
        FakeRouter.request_body = None
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

    def test_production_router_has_only_localhost_route(self) -> None:
        self.assertEqual(ROUTER_BASE_URL, "http://localhost:20128")
        self.assertEqual(RouterClient("free-tier-alias").endpoint, "http://localhost:20128/v1/chat/completions")

    def test_blank_alias_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RouterClient("   ")


if __name__ == "__main__":
    unittest.main()
