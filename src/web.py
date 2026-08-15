"""Local browser chat for Sage Foundation."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from events import EventStore
from router import RouterClient

STATIC_ROOT = Path(__file__).with_name("static")
MAX_REQUEST_BYTES = 64 * 1024
ROUTER_FAILURE = "Sage could not reach the local router. Your message was saved; no assistant reply was recorded."
SAVE_FAILURE = "Sage received a reply but could not save it. No assistant reply was recorded."


class SageServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], store: EventStore, router: RouterClient) -> None:
        super().__init__(address, SageHandler)
        self.store = store
        self.router = router


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
            self._json(HTTPStatus.OK, {"events": self.server.store.read_all()})
        elif path == "/health":
            self._json(HTTPStatus.OK, {"ok": True})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._trusted_host() or not self._same_origin():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if urlparse(self.path).path != "/api/chat":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "content type must be application/json"})
            return
        try:
            length = int(self.headers["Content-Length"])
        except (KeyError, ValueError):
            self._json(HTTPStatus.LENGTH_REQUIRED, {"error": "content length is required"})
            return
        if not 0 < length <= MAX_REQUEST_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "message is too large"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            message = body["message"].strip()
        except (KeyError, TypeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "message must be a nonblank string"})
            return
        if not message:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "message must be a nonblank string"})
            return

        try:
            self.server.store.append("user", message)
        except OSError:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Sage could not save your message. Nothing was sent."})
            return

        self._stream_reply(self.server.router.stream(message))

    def _stream_reply(self, chunks: Iterator[str]) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        reply: list[str] = []
        completed = False
        ended = False
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
            try:
                self.server.store.append("assistant", "".join(reply))
            except OSError:
                self._write_chunk(SAVE_FAILURE)
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
                ended = True
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        if not ended:
            return

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
    server = SageServer(("127.0.0.1", port), EventStore(data_root), RouterClient(alias))
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
