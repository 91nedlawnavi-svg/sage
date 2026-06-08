import httpx
import json
from config.settings import (
    NVIDIA_API_KEY,
    CHAT_API_URL,
    CHAT_MODEL,
    CHAT_TEMPERATURE,
    CHAT_MAX_TOKENS,
    CHAT_TOP_P,
)


async def chat_stream(messages: list[dict], client: httpx.AsyncClient):
    """Stream chat completion from NVIDIA NIM."""
    if not NVIDIA_API_KEY:
        yield "⚠️ NVIDIA_API_KEY not set"
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
            timeout=httpx.Timeout(
                connect=10.0, read=180.0, write=10.0, pool=5.0
            ),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]  # strip "data: "
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    content = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue
    except httpx.ConnectError:
        yield "⚠️ Connection error: could not reach NVIDIA NIM"
    except httpx.HTTPStatusError as e:
        yield f"⚠️ HTTP error: {e.response.status_code} - {e.response.text}"
    except Exception as e:
        yield f"⚠️ Unexpected error: {e}"


async def nim_complete(
    system: str,
    user: str,
    client: httpx.AsyncClient,
    model: str = CHAT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 512,
) -> str | None:
    """Non-streaming completion for reflection/synthesis (future phase)."""
    if not NVIDIA_API_KEY:
        return None

    try:
        response = await client.post(
            CHAT_API_URL,
            headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=5.0),
        )
        response.raise_for_status()
        data = response.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    except Exception:
        return None