"""Sage's sole inference boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http.client import HTTPResponse, IncompleteRead
from typing import Final, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROUTER_BASE_URL: Final = "http://localhost:20128"


@dataclass(frozen=True)
class RouterResult:
    reply: str | None

    @property
    def succeeded(self) -> bool:
        return self.reply is not None


class RouterClient:
    def __init__(self, alias: str, base_url: str = ROUTER_BASE_URL) -> None:
        if not alias.strip():
            raise ValueError("Free-tier alias is required")
        self.alias = alias
        self.endpoint = f"{base_url}/v1/chat/completions"

    def _request(self, messages: list[dict[str, str]], *, stream: bool) -> Request:
        payload: dict[str, object] = {"model": self.alias, "messages": messages}
        if stream:
            payload["stream"] = True
        encoded_payload = json.dumps(payload).encode()
        return Request(
            self.endpoint,
            data=encoded_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    def chat(self, message: str) -> RouterResult:
        return self.chat_with_messages([{"role": "user", "content": message}])

    def chat_with_messages(self, messages: list[dict[str, str]]) -> RouterResult:
        try:
            with urlopen(self._request(messages, stream=False), timeout=30) as response:
                body = json.load(response)
        except (HTTPError, URLError, OSError, IncompleteRead, UnicodeDecodeError, json.JSONDecodeError):
            return RouterResult(reply=None)

        try:
            reply = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return RouterResult(reply=None)
        return RouterResult(reply=reply) if isinstance(reply, str) and reply.strip() else RouterResult(reply=None)

    def stream(self, message: str) -> Iterator[str]:
        return self.stream_with_messages([{"role": "user", "content": message}])

    def stream_with_messages(self, messages: list[dict[str, str]]) -> Iterator[str]:
        try:
            response = urlopen(self._request(messages, stream=True), timeout=30)
        except (HTTPError, URLError, OSError, IncompleteRead):
            return

        with response:
            yield from self._stream_response(response)
    @staticmethod
    def _stream_response(response: HTTPResponse) -> Iterator[str]:
        completed = False
        try:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    completed = True
                    break
                try:
                    content = json.loads(data)["choices"][0]["delta"].get("content")
                except (KeyError, IndexError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
                    return
                if isinstance(content, str) and content:
                    yield content
        except (OSError, IncompleteRead, UnicodeDecodeError):
            return
        if not completed:
            return
        yield ""
