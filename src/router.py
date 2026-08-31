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
DEFAULT_CHAT_MODELS: Final = (
    "xk/qwen/qwen3.8-max:free",
    "xk/deepseek/deepseek-v4-pro",
    "xk/deepseek/deepseek-v4-flash",
)

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
    """Inference client for free-tier router aliases, with ordered failover."""

    def __init__(self, alias: str | list[str] | tuple[str, ...], base_url: str = ROUTER_BASE_URL) -> None:
        aliases = (alias,) if isinstance(alias, str) else tuple(alias)
        self.aliases = tuple(item.strip() for item in aliases if item.strip())
        if not self.aliases:
            raise ValueError("At least one free-tier alias is required")
        self.alias = self.aliases[0]
        self.endpoint = f"{base_url}/v1/chat/completions"

    def _request(
        self,
        messages: list[dict[str, str]],
        *,
        stream: bool,
        temperature: float | None = None,
        max_tokens: int | None = None,
        alias: str | None = None,
    ) -> Request:
        payload: dict[str, object] = {
            "model": alias or self.alias,
            "messages": messages,
        }
        if stream:
            payload["stream"] = True
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        encoded_payload = json.dumps(payload).encode()
        return Request(
            self.endpoint,
            data=encoded_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    def chat(self, message: str) -> RouterResult:
        return self.chat_with_messages([{"role": "user", "content": message}])

    def chat_with_messages(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        timeout: float = 60,
        max_tokens: int | None = None,
    ) -> RouterResult:
        for alias in self.aliases:
            result = self._chat_once(alias, messages, temperature=temperature, timeout=timeout, max_tokens=max_tokens)
            if result.succeeded:
                return result
        return RouterResult(reply=None)

    def _chat_once(
        self,
        alias: str,
        messages: list[dict[str, str]],
        *,
        temperature: float | None,
        timeout: float,
        max_tokens: int | None,
    ) -> RouterResult:
        try:
            with urlopen(
                self._request(messages, stream=False, temperature=temperature, max_tokens=max_tokens, alias=alias),
                timeout=timeout,
            ) as response:
                body, _ = json.JSONDecoder().raw_decode(response.read().decode("utf-8").lstrip())
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
        if reasoning and reply.strip() == str(reasoning).strip():
            return RouterResult(reply=None)

        cleaned = strip_reasoning(reply)
        return RouterResult(reply=cleaned) if cleaned else RouterResult(reply=None)

    def stream(self, message: str) -> Iterator[str]:
        return self.stream_with_messages([{"role": "user", "content": message}])

    def stream_with_messages(self, messages: list[dict[str, str]], *, temperature: float = 0.7) -> Iterator[str]:
        for alias in self.aliases:
            try:
                response = urlopen(self._request(messages, stream=True, temperature=temperature, alias=alias), timeout=60)
            except (HTTPError, URLError, OSError, IncompleteRead):
                continue

            emitted = False
            with response:
                for chunk in self._stream_response(response):
                    if chunk:
                        emitted = True
                    yield chunk
            if emitted:
                return

    @staticmethod
    def _stream_response(response: HTTPResponse) -> Iterator[str]:
        completed = False
        emitted = False
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
                        emitted = True
                        yield content
        except (OSError, IncompleteRead, UnicodeDecodeError):
            return
        if completed and emitted:
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
