"""Sage's sole inference boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http.client import IncompleteRead
from typing import Final
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

    def chat(self, message: str) -> RouterResult:
        payload = json.dumps(
            {
                "model": self.alias,
                "messages": [{"role": "user", "content": message}],
            }
        ).encode()
        request = Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                body = json.load(response)
        except (HTTPError, URLError, OSError, IncompleteRead, UnicodeDecodeError, json.JSONDecodeError):
            return RouterResult(reply=None)

        try:
            reply = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return RouterResult(reply=None)
        return RouterResult(reply=reply) if isinstance(reply, str) and reply.strip() else RouterResult(reply=None)
