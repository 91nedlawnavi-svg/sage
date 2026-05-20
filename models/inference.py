"""
models/inference.py — LLM inference wrappers

Two endpoints:
  chat_stream()  → streaming tokens from NVIDIA NIM
  mem_complete() → single completion from local memory model (8081)
"""

import json
from typing import AsyncIterator, Optional

import httpx

from config import (
    NVIDIA_API_KEY,
    CHAT_API_URL,
    CHAT_MODEL,
    CHAT_MAX_TOKENS,
    CHAT_TEMPERATURE,
    CHAT_TOP_P,
    MEM_API_URL,
    MEM_MAX_TOKENS,
    MEM_TEMPERATURE,
    MEM_TOP_P,
)

from utils.logger import log


async def chat_stream(
    messages: list[dict],
    client: httpx.AsyncClient,
) -> AsyncIterator[str]:
    """
    Streaming NVIDIA NIM chat completion wrapper.
    Yields string tokens as they arrive via SSE.
    Yields an error string on failure.
    """

    if not NVIDIA_API_KEY:
        yield "\n\n⚠️ NVIDIA_API_KEY not set. Check your ~/.bashrc."
        return

    try:
        async with client.stream(
            "POST",
            CHAT_API_URL,
            headers={
                "Authorization": f"Bearer {NVIDIA_API_KEY}",
                "Accept": "text/event-stream",
            },
            json={
                "model": CHAT_MODEL,
                "messages": messages,
                "stream": True,
                "temperature": CHAT_TEMPERATURE,
                "max_tokens": CHAT_MAX_TOKENS,
                "top_p": CHAT_TOP_P,
            },
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=5.0),
        ) as resp:
            resp.raise_for_status()

            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    token = (
                        chunk.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content", "")
                    )
                    if token:
                        yield token
                except json.JSONDecodeError:
                    continue

    except httpx.ConnectError:
        yield "\n\n⚠️ Cannot reach NVIDIA NIM. Check internet connection."

    except httpx.HTTPStatusError as e:
        try:
            await e.response.aread()
            body = e.response.text
        except Exception:
            body = "(unreadable)"
        yield f"\n\n⚠️ NVIDIA API error {e.response.status_code}: {body}"

    except Exception as e:
        yield f"\n\n⚠️ Chat error: {e}"


async def mem_complete(
    system: str,
    user: str,
    client: httpx.AsyncClient,
    max_tokens: int = MEM_MAX_TOKENS,
) -> Optional[str]:
    """
    Single (non-streaming) completion from the local memory model.
    Returns the response text, or None on failure.
    """

    try:
        resp = await client.post(
            MEM_API_URL,
            json={
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                "temperature": MEM_TEMPERATURE,
                "max_tokens": max_tokens,
                "top_p": MEM_TOP_P,
            },
            timeout=90.0,
        )

        resp.raise_for_status()

        return (
            resp.json()["choices"][0]["message"]["content"].strip()
        )

    except Exception as e:
        log("inference", "mem_complete_error", error=str(e))
        return None
