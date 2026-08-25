"""Local browser chat for Sage Foundation with Notebook and interior data APIs."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlparse

from events import EventStore
from interior import InteriorStore
from router import EmbeddingClient, RouterClient
from sage import HELD_CLOSE_ACKNOWLEDGEMENT, ROUTER_FAILURE, SAVE_FAILURE, accept_message, build_router_messages

STATIC_ROOT = Path(__file__).with_name("static")
MAX_REQUEST_BYTES = 64 * 1024
SAVE_REPLY_FAILURE = "Sage received a reply but could not save it. No assistant reply was recorded."


class SageServer(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        store: EventStore,
        router: RouterClient,
        interior: InteriorStore | None = None,
    ) -> None:
        super().__init__(address, SageHandler)
        self.store = store
        self.router = router
        self.interior = interior or InteriorStore(store.data_root)


class SageHandler(BaseHTTPRequestHandler):
    server: SageServer

    def do_GET(self) -> None:
        if not self._trusted_host():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        path = urlparse(self.path).path
        if path == "/":
            self._serve_static("index.html", "text/html; charset=utf-8")
        elif path == "/static/app.css":
            self._serve_static("app.css", "text/css; charset=utf-8")
        elif path == "/static/app.js":
            self._serve_static("app.js", "application/javascript; charset=utf-8")
        elif path == "/api/history":
            events = self.server.store.read_all()
            waiting = self.server.interior.get_waiting_message()
            if waiting and not waiting.get("read"):
                # Prepend waiting message as active turn
                events = [
                    {
                        "id": "waiting_message",
                        "role": "assistant",
                        "content": waiting["content"],
                        "said_at": waiting["said_at"],
                        "kind": "waiting",
                    }
                ] + events
            self._json(HTTPStatus.OK, {"events": events})
        elif path == "/reflections" or path == "/api/reflections":
            self._json(HTTPStatus.OK, {"reflections": self.server.interior.list_reflections()})
        elif path == "/api/beliefs":
            self._json(HTTPStatus.OK, {"beliefs": self.server.interior.list_beliefs()})
        elif path == "/api/entities":
            self._json(HTTPStatus.OK, {"entities": self.server.store.entity_observations()})
        elif path == "/health":
            self._json(HTTPStatus.OK, {"ok": True})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._trusted_host() or not self._same_origin():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        path = urlparse(self.path).path
        if path == "/api/chat":
            self._chat()
            return
        if path == "/api/waiting-message/ack":
            self.server.interior.clear_waiting_message()
            self._json(HTTPStatus.OK, {"ok": True})
            return
        event_id = self._privacy_target(path)
        if event_id is not None:
            self._privacy_override(event_id)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _chat(self) -> None:
        body = self._json_body()
        if body is None:
            return
        message = body.get("message")
        if not isinstance(message, str) or not (message := message.strip()):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "message must be a nonblank string"})
            return
        held_close_mode = body.get("held_close_mode", False)
        if not isinstance(held_close_mode, bool):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "held_close_mode must be boolean"})
            return
        accepted = accept_message(message, self.server.store, held_close=held_close_mode)
        if accepted is None:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": SAVE_FAILURE})
            return

        # Acknowledge/clear waiting message once user speaks
        self.server.interior.clear_waiting_message()

        headers = {
            "X-Sage-Event-ID": accepted.event["id"],
            "X-Sage-Held-Close": str(accepted.privacy.held_close).lower(),
        }
        if accepted.privacy.held_close:
            self._stream_reply(iter((HELD_CLOSE_ACKNOWLEDGEMENT, "")), headers, persist_reply=False)
            return
        self._stream_reply(
            self.server.router.stream_with_messages(
                build_router_messages(message, self.server.store, exclude_event_id=accepted.event["id"])
            ),
            headers,
            persist_reply=True,
        )

    def _privacy_override(self, event_id: str) -> None:
        body = self._json_body()
        if body is None:
            return
        held_close = body.get("held_close")
        if not isinstance(held_close, bool):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "held_close must be boolean"})
            return
        try:
            updated = self.server.store.set_held_close(event_id, held_close)
        except OSError:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Sage could not save privacy setting."})
            return
        if not updated:
            self._json(HTTPStatus.NOT_FOUND, {"error": "user event not found"})
            return
        self._json(HTTPStatus.OK, {"event_id": event_id, "held_close": held_close})

    def _json_body(self) -> dict[str, object] | None:
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "content type must be application/json"})
            return None
        try:
            length = int(self.headers["Content-Length"])
        except (KeyError, ValueError):
            self._json(HTTPStatus.LENGTH_REQUIRED, {"error": "content length is required"})
            return None
        if not 0 < length <= MAX_REQUEST_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "message is too large"})
            return None
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "body must be JSON"})
            return None
        if not isinstance(body, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "body must be an object"})
            return None
        return body

    @staticmethod
    def _privacy_target(path: str) -> str | None:
        prefix = "/api/events/"
        suffix = "/privacy"
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        event_id = unquote(path[len(prefix):-len(suffix)])
        return event_id if event_id and "/" not in event_id else None

    def _stream_reply(self, chunks: Iterator[str], headers: dict[str, str], *, persist_reply: bool) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-cache")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()

        reply: list[str] = []
        completed = False
        try:
            for chunk in chunks:
                if chunk == "":
                    completed = True
                    break
                reply.append(chunk)
                self._write_chunk(chunk)
            if not completed or not reply:
                self._write_chunk(ROUTER_FAILURE)
                return
            if persist_reply:
                try:
                    self.server.store.append("assistant", "".join(reply))
                except OSError:
                    self._write_chunk(SAVE_REPLY_FAILURE)
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def _trusted_host(self) -> bool:
        host = self.headers.get("Host", "")
        return host in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{self.headers['Host']}"

    def _write_chunk(self, text: str) -> None:
        data = text.encode()
        self.wfile.write(f"{len(data):X}\r\n".encode())
        self.wfile.write(data + b"\r\n")
        self.wfile.flush()

    def _json(self, status: HTTPStatus, body: object) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, name: str, content_type: str) -> None:
        try:
            data = (STATIC_ROOT / name).read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def run(alias: str, data_root: Path | None = None, port: int = 6969) -> None:
    embedder = EmbeddingClient()
    store = EventStore(data_root, embedder=embedder)
    router = RouterClient(alias)
    interior = InteriorStore(data_root)
    server = SageServer(("127.0.0.1", port), store, router, interior)
    print(f"Sage listening at http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sage local browser chat.")
    parser.add_argument("--alias", required=True, help="Configured free-tier router alias")
    parser.add_argument("--data-root", type=Path, help="Event directory; defaults to ~/sage_data")
    parser.add_argument("--port", type=int, default=6969, help="Local browser port")
    args = parser.parse_args()
    run(args.alias, args.data_root, args.port)


if __name__ == "__main__":
    main()
