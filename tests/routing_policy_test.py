"""Sealed routing-policy checks. No network, providers, or Sage data."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from config.settings import CHAT_MODEL, EXTRACTION_SCRIBE_MODEL
from models.inference.engine import chat_stream, nim_complete

FIXTURES = Path(__file__).with_name("fixtures") / "routing_policy.jsonl"
REQUIRED_IDS = {
    "chat-single-alias", "scribe-single-alias", "chat-http-error",
    "chat-connect-error", "chat-timeout", "held-close-local", "no-fallback",
}


class _Response:
    def __init__(self, status_code=200, lines=(), payload=None):
        self.status_code = status_code
        self._lines = lines
        self._payload = payload or {"choices": [{"message": {"content": "done"}}]}

    async def aread(self):
        return b"router error"

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("failed", request=None, response=self)

    def json(self):
        return self._payload


class _Stream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_):
        return False


class _Client:
    def __init__(self, response=None, error=None):
        self.response = response or _Response()
        self.error = error
        self.calls = []

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        return _Stream(self.response)

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if self.error:
            raise self.error
        return self.response


def test_fixture_schema():
    rows = [json.loads(line) for line in FIXTURES.read_text().splitlines() if line]
    assert {row["id"] for row in rows} == REQUIRED_IDS
    assert len(rows) == len(REQUIRED_IDS)
    assert all(isinstance(row["id"], str) and row["surface"] for row in rows)
    assert all("requests" in row or "provider_requests" in row for row in rows)
    held = next(row for row in rows if row["id"] == "held-close-local")
    assert held["provider_requests"] == 0
    no_fallback = next(row for row in rows if row["id"] == "no-fallback")
    assert no_fallback["policy"] == "no-alias-substitution-or-retry"


async def test_single_alias_and_failures():
    chat = _Client(_Response(lines=[
        'data: {"choices":[{"delta":{"content":"hello"}}]}', "data: [DONE]"]))
    assert [chunk async for chunk in chat_stream([], chat)] == ["hello"]
    assert len(chat.calls) == 1
    assert chat.calls[0][2]["json"]["model"] == CHAT_MODEL

    scribe = _Client()
    assert await nim_complete("system", "user", scribe, EXTRACTION_SCRIBE_MODEL) == "done"
    assert len(scribe.calls) == 1
    assert scribe.calls[0][2]["json"]["model"] == EXTRACTION_SCRIBE_MODEL

    for error in (None, httpx.ConnectError("down"), httpx.ReadTimeout("slow")):
        client = _Client(_Response(status_code=503) if error is None else None, error)
        chunks = [chunk async for chunk in chat_stream([], client)]
        assert len(client.calls) == 1
        assert len(chunks) == 1 and json.loads(chunks[0].strip("\x1e"))["event"] == "error"

    failed_scribe = _Client(error=httpx.ConnectError("down"))
    assert await nim_complete("system", "user", failed_scribe, EXTRACTION_SCRIBE_MODEL) is None
    assert len(failed_scribe.calls) == 1


if __name__ == "__main__":
    test_fixture_schema()
    asyncio.run(test_single_alias_and_failures())
    print("OK routing_policy_test")
