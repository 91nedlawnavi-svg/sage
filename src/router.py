"""Inference and embedding clients for Sage."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from http.client import HTTPResponse, IncompleteRead
from typing import Final, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROUTER_BASE_URL: Final = "http://localhost:20128"
EMBEDDER_BASE_URL: Final = "http://127.0.0.1:8081"

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Remove reasoning preamble (<think>...</think>) leaked by reasoning models."""
    text = _THINK_RE.sub("", text)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1]
    return text.strip()


@dataclass(frozen=True)
class RouterResult:
    reply: str | None

    @property
    def succeeded(self) -> bool:
        return self.reply is not None


class RouterClient:
    """Inference client for free-tier router aliases (chat model and scribe model)."""

    def __init__(self, alias: str, base_url: str = ROUTER_BASE_URL) -> None:
        if not alias.strip():
            raise ValueError("Free-tier alias is required")
        self.alias = alias.strip()
        self.endpoint = f"{base_url}/v1/chat/completions"

    def _request(self, messages: list[dict[str, str]], *, stream: bool, temperature: float | None = None) -> Request:
        payload: dict[str, object] = {
            "model": self.alias,
            "messages": messages,
        }
        if stream:
            payload["stream"] = True
        if temperature is not None:
            payload["temperature"] = temperature
        encoded_payload = json.dumps(payload).encode()
        return Request(
            self.endpoint,
            data=encoded_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    def chat(self, message: str) -> RouterResult:
        return self.chat_with_messages([{"role": "user", "content": message}])

    def chat_with_messages(self, messages: list[dict[str, str]], *, temperature: float | None = None) -> RouterResult:
        try:
            with urlopen(self._request(messages, stream=False, temperature=temperature), timeout=60) as response:
                body = json.load(response)
        except (HTTPError, URLError, OSError, IncompleteRead, UnicodeDecodeError, json.JSONDecodeError):
            return RouterResult(reply=None)

        try:
            choice = body["choices"][0]
            message_obj = choice.get("message", {})
            reply = message_obj.get("content")
            reasoning = message_obj.get("reasoning") or message_obj.get("reasoning_content") or ""
        except (KeyError, IndexError, TypeError):
            return RouterResult(reply=None)

        if not isinstance(reply, str) or not reply.strip():
            return RouterResult(reply=None)

        # Drop truncated mid-reasoning outputs
        if reasoning and reply.strip() == str(reasoning).strip():
            return RouterResult(reply=None)

        cleaned = strip_reasoning(reply)
        return RouterResult(reply=cleaned) if cleaned else RouterResult(reply=None)

    def stream(self, message: str) -> Iterator[str]:
        return self.stream_with_messages([{"role": "user", "content": message}])

    def stream_with_messages(self, messages: list[dict[str, str]], *, temperature: float = 0.7) -> Iterator[str]:
        try:
            response = urlopen(self._request(messages, stream=True, temperature=temperature), timeout=60)
        except (HTTPError, URLError, OSError, IncompleteRead):
            return

        with response:
            yield from self._stream_response(response)

    @staticmethod
    def _stream_response(response: HTTPResponse) -> Iterator[str]:
        completed = False
        in_think_block = False
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
                    delta = json.loads(data)["choices"][0].get("delta", {})
                    content = delta.get("content")
                except (KeyError, IndexError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
                    return
                if isinstance(content, str) and content:
                    if "<think>" in content:
                        in_think_block = True
                        content = content.split("<think>", 1)[0]
                    if in_think_block:
                        if "</think>" in content:
                            in_think_block = False
                            content = content.split("</think>", 1)[-1]
                        else:
                            content = ""
                    if content:
                        yield content
        except (OSError, IncompleteRead, UnicodeDecodeError):
            return
        if not completed:
            return
        yield ""


class EmbeddingClient:
    """Local embedding server client running on 127.0.0.1:8081."""

    def __init__(self, base_url: str = EMBEDDER_BASE_URL) -> None:
        self.endpoint = f"{base_url}/v1/embeddings"

    def embed(self, text: str) -> list[float] | None:
        cleaned = text.strip()
        if not cleaned:
            return None
        payload = json.dumps({"input": cleaned}).encode()
        request = Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                body = json.load(response)
            data = body.get("data", [])
            if data and isinstance(data, list):
                vector = data[0].get("embedding")
                if isinstance(vector, list) and len(vector) > 0:
                    return [float(x) for x in vector]
        except (HTTPError, URLError, OSError, IncompleteRead, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        return None
