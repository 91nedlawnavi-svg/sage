"""Local browser chat for Sage with Notebook and interior data APIs."""

from __future__ import annotations
import re

import argparse
import ipaddress
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlparse

from events import EventStore
from interior import InteriorStore
from router import EmbeddingClient, RouterClient
from sage import SENSITIVE_ACKNOWLEDGEMENT, ROUTER_FAILURE, SAVE_FAILURE, accept_message, build_router_messages, compose_identity_block, load_directive
from search import search, format_search_context

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
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if not self._trusted_host():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        path = urlparse(self.path).path
        if path == "/":
            self._serve_static("index.html", "text/html; charset=utf-8")
        elif path == "/static/app.css":
            self._serve_static("app.css", "text/css; charset=utf-8")
        elif path == "/notebook":
            self._serve_static("notebook.html", "text/html; charset=utf-8")
        elif path == "/static/app.js":
            self._serve_static("app.js", "application/javascript; charset=utf-8")
        elif path == "/static/notebook.js":
            self._serve_static("notebook.js", "application/javascript; charset=utf-8")
        elif path == "/api/history":
            events = self.server.store.visible_history()
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
            self._json(HTTPStatus.OK, {"events": events, "model": self.server.router.last_alias})
        elif path == "/reflections" or path == "/api/reflections":
            self._json(HTTPStatus.OK, {"reflections": self.server.interior.list_reflections()})
        elif path == "/api/beliefs":
            self._json(HTTPStatus.OK, {"beliefs": self.server.interior.list_beliefs()})
        elif path == "/api/entities":
            self._json(HTTPStatus.OK, {"entities": self.server.store.entity_observations()})
        elif path == "/api/identity":
            self._json(HTTPStatus.OK, {"identity": self.server.interior.list_identity()})
        elif path == "/api/metabolism":
            records = []
            if self.server.interior.metabolism_path.exists():
                for line in self.server.interior.metabolism_path.read_text(encoding="utf-8").splitlines():
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            self._json(HTTPStatus.OK, {"metabolism": records[-20:]})
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
        if path == "/api/chat/clear":
            try:
                self.server.store.append_chat_boundary()
                self.server.interior.clear_waiting_message()
            except OSError:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Sage could not start a new chat."})
                return
            self._json(HTTPStatus.OK, {"ok": True})
            return
        identity_target = self._identity_target(path)
        if identity_target is not None:
            self._identity_ruling(*identity_target)
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
        sensitive_mode = body.get("sensitive_mode", False)
        if not isinstance(sensitive_mode, bool):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "sensitive_mode must be boolean"})
            return
        accepted = accept_message(message, self.server.store, sensitive=sensitive_mode)
        if accepted is None:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": SAVE_FAILURE})
            return

        # Acknowledge/clear waiting message once user speaks
        self.server.interior.clear_waiting_message()

        headers = {
            "X-Sage-Event-ID": accepted.event["id"],
            "X-Sage-Sensitive": str(accepted.privacy.sensitive).lower(),
        }
        self._begin_stream(headers)
        if accepted.privacy.sensitive:
            self._stream_reply(iter((SENSITIVE_ACKNOWLEDGEMENT, "")), persist_reply=False)
            return

        # Decide and run search with visible stream events
        self._search_decision_failed = False
        search_query = self._decide_search(message, accepted.event["id"])
        search_context = ""
        if search_query:
            self._write_stream_event("search", search_query)
            results = search(search_query)
            if results:
                search_context = format_search_context(results)
                self._write_stream_event("search_done", f"{len(results)} results")
                try:
                    sources = "\n".join(r.url for r in results)
                    self.server.store.append(
                        "assistant",
                        f"[Web search: {search_query}]\nSources: {sources}",
                        save_embedding=True,
                    )
                except OSError:
                    pass
            else:
                self._write_stream_event("search_error", "Search returned no results")
        elif self._search_decision_failed:
            self._write_stream_event("search_error", "Could not decide whether to search")

        self._stream_reply(
            self.server.router.stream_with_messages(
                build_router_messages(
                    message,
                    self.server.store,
                    exclude_event_id=accepted.event["id"],
                    directive=load_directive(identity_block=compose_identity_block(self.server.interior)),
                    search_context=search_context,
                )
            ),
            persist_reply=True,
        )

    def _decide_search(self, message: str, exclude_event_id: str) -> str | None:
        """Ask the model if web search is needed. Sets _search_decision_failed on router error."""
        directive = load_directive(identity_block=compose_identity_block(self.server.interior))
        decision_prompt = (
            "Based on the user's message and your knowledge, do you need to search the web "
            "to answer accurately? Reply with ONLY a search query if yes, or 'NO' if no.\n\n"
            f"User message: {message}"
        )
        messages = [{"role": "system", "content": directive}, {"role": "user", "content": decision_prompt}]
        result = self.server.router.chat_with_messages(messages, temperature=0.0, max_tokens=100)
        if not result.succeeded:
            self._search_decision_failed = True
            return None
        reply = result.reply.strip()
        if reply.upper() == "NO" or not reply:
            return None
        # Extract query from [search: ...] tag or use raw reply
        match = re.search(r"\[search:\s*(.+?)\]", reply, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        # If reply looks like a query (no full sentences), use it
        if len(reply) < 200 and "\n" not in reply and not reply.endswith("."):
            return reply
        return None

    def _privacy_override(self, event_id: str) -> None:
        body = self._json_body()
        if body is None:
            return
        sensitive = body.get("sensitive")
        if not isinstance(sensitive, bool):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "sensitive must be boolean"})
            return
        try:
            updated = self.server.store.set_sensitive(event_id, sensitive)
        except OSError:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Sage could not save privacy setting."})
            return
        if not updated:
            self._json(HTTPStatus.NOT_FOUND, {"error": "user event not found"})
            return
        self._json(HTTPStatus.OK, {"event_id": event_id, "sensitive": sensitive})

    def _identity_ruling(self, entry_id: str, action: str) -> None:
        verdict = "ratified" if action == "ratify" else "rejected"
        entries = self.server.interior.list_identity()
        if not any(e["id"] == entry_id for e in entries):
            self._json(HTTPStatus.NOT_FOUND, {"error": "identity entry not found"})
            return
        try:
            self.server.interior.append_identity_ruling(entry_id, verdict)
        except (OSError, ValueError) as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        self._json(HTTPStatus.OK, {"id": entry_id, "verdict": verdict})

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

    @staticmethod
    def _identity_target(path: str) -> tuple[str, str] | None:
        """Parse /api/identity/<id>/ratify or /api/identity/<id>/reject."""
        prefix = "/api/identity/"
        if not path.startswith(prefix):
            return None
        rest = path[len(prefix):]
        if rest.endswith("/ratify"):
            entry_id = unquote(rest[:-len("/ratify")])
            action = "ratify"
        elif rest.endswith("/reject"):
            entry_id = unquote(rest[:-len("/reject")])
            action = "reject"
        else:
            return None
        return (entry_id, action) if entry_id and "/" not in entry_id else None

    def _begin_stream(self, headers: dict[str, str]) -> None:
        """Send the response head before any stream event; chunks written earlier corrupt the response."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-cache")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()

    def _stream_reply(self, chunks: Iterator[str], *, persist_reply: bool) -> None:
        reply: list[str] = []
        completed = False
        try:
            for chunk in chunks:
                if chunk == "":
                    completed = True
                    break
                reply.append(chunk)
                self._write_stream_event("delta", chunk)
            if not completed or not reply:
                self._write_stream_event("error", ROUTER_FAILURE)
                return
            if persist_reply:
                try:
                    self.server.store.append("assistant", "".join(reply))
                except OSError:
                    self._write_stream_event("error", SAVE_REPLY_FAILURE)
                    return
                # only a real provider reply names a model; the sensitive acknowledgement does not
                self._write_stream_event("model", self.server.router.last_alias)
            self._write_stream_event("done")
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
        name, separator, port = host.rpartition(":")
        if not separator or port != str(self.server.server_port):
            return False
        if name == "localhost":
            return True
        try:
            ipaddress.ip_address(name.strip("[]"))
        except ValueError:
            return False
        return True

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{self.headers['Host']}"

    def _write_chunk(self, text: str) -> None:
        data = text.encode()
        self.wfile.write(f"{len(data):X}\r\n".encode())
        self.wfile.write(data + b"\r\n")
        self.wfile.flush()

    def _write_stream_event(self, event_type: str, content: str | None = None) -> None:
        event = {"type": event_type}
        if content is not None:
            event["content"] = content
        self._write_chunk(json.dumps(event, ensure_ascii=False) + "\n")

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
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def run(alias: str, data_root: Path | None = None, port: int = 6969) -> None:
    embedder = EmbeddingClient()
    store = EventStore(data_root, embedder=embedder)
    router = RouterClient(alias)
    interior = InteriorStore(data_root)
    server = SageServer(("0.0.0.0", port), store, router, interior)
    print(f"Sage listening on http://0.0.0.0:{server.server_port}")
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
