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
ROUTER_FAILURE = "Sage could not reach the local router. Your message was saved; no assistant reply was recorded."


class SageServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], store: EventStore, router: RouterClient) -> None:
        super().__init__(address, SageHandler)
        self.store = store
        self.router = router


class SageHandler(BaseHTTPRequestHandler):
    server: SageServer

    def do_GET(self) -> None:
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
        if urlparse(self.path).path != "/api/chat":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            message = body["message"].strip()
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
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
        try:
            for chunk in chunks:
                if chunk == "":
                    completed = True
                    break
                reply.append(chunk)
                self._write_chunk(chunk)
            if completed and reply:
                self.server.store.append("assistant", "".join(reply))
            elif not completed:
                self._write_chunk(ROUTER_FAILURE)
        except (BrokenPipeError, ConnectionResetError):
            return
        except OSError:
            return
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

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
