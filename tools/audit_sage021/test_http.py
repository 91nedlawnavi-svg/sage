"""HTTP error reporting and reply persistence: synthetic data, offline router.

These intended-behavior tests preserve failures against the audit baseline.
"""
import http.client
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from events import EventStore
from interior import InteriorStore
from router import RouterResult
from web import SageHandler, SageServer


class OfflineRouter:
    aliases = ("offline",)
    last_alias = "offline"

    def chat_with_messages(self, *args, **kwargs):
        return RouterResult("NO", "offline")

    def stream_with_messages(self, *args, **kwargs):
        return iter(("Synthetic answer", ""))


class HttpAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sage021-http-")
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root)
        self.interior = InteriorStore(self.root)
        self.server = SageServer(("127.0.0.1", 0), self.store, OfflineRouter(), self.interior)
        self.errors = []
        self.server.handle_error = lambda *args: self.errors.append(repr(sys.exception()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def request(self, body):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.request("POST", "/api/chat", body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, response.read()
        except http.client.RemoteDisconnected:
            return None, b"connection closed without HTTP response"
        finally:
            connection.close()

    def test_missing_content_length_returns_client_error(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.putrequest("POST", "/api/chat")
            connection.putheader("Content-Type", "application/json")
            connection.endheaders()
            try:
                response = connection.getresponse()
                status = response.status
                response.read()
            except http.client.RemoteDisconnected:
                status = None
        finally:
            connection.close()
        self.assertEqual(status, 411, f"errors={self.errors}")

    def test_invalid_utf8_returns_client_error(self):
        status, _ = self.request(b'{"message":"\xff"}')
        self.assertEqual(status, 400, f"errors={self.errors}")

    def test_invalid_json_returns_client_error(self):
        status, _ = self.request(b'{broken')
        self.assertEqual(status, 400)

    def test_disconnect_before_completion_leaves_accepted_user_intact(self):
        accepted = self.store.append("user", "SYNTHETIC_QUESTION")
        handler = object.__new__(SageHandler)
        handler.server = self.server
        handler.wfile = io.BytesIO()
        handler._write_stream_event = Mock(side_effect=BrokenPipeError("display disconnected"))
        handler._stream_reply(iter(("SYNTHETIC_COMPLETE_ANSWER", "")), persist_reply=True,
                              session_id=accepted["session_id"], event_id=accepted["id"])
        # Characterization only: completion marker was never consumed. This
        # establishes interruption behavior, not loss of a validated reply.
        self.assertEqual(self.store.history(), [accepted])


if __name__ == "__main__":
    unittest.main()
