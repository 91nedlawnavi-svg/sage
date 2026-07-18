import httpx
import json
import re
from config.settings import (
    NVIDIA_API_KEY,
    CHAT_API_URL,
    CHAT_MODEL,
    CHAT_TEMPERATURE,
    CHAT_MAX_TOKENS,
    CHAT_TOP_P,
)
from utils.logger import warning


def error_frame(message: str) -> str:
    """Control frame (\\x1e-delimited JSON) — the UI renders it; the chat
    path must never persist it as Sage's speech."""
    return "\x1e" + json.dumps({"event": "error", "message": message}) + "\x1e"


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Remove reasoning preamble some models leak into content.

    Handles both a full <think>...</think> block and a truncated tail where
    only the closing tag survives (everything before it is reasoning).
    """
    text = _THINK_RE.sub("", text)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1]
    return text.strip()


async def chat_stream(messages: list[dict], client: httpx.AsyncClient):
    """Stream chat completion from the local router (Omniroute).

    Errors are yielded as control frames (see error_frame), never as prose —
    callers must pass frames through to the UI and exclude them from any
    persisted reply.
    """
    try:
        headers = {"Accept": "text/event-stream"}
        if NVIDIA_API_KEY and NVIDIA_API_KEY.strip():
            headers["Authorization"] = f"Bearer {NVIDIA_API_KEY.strip()}"

        async with client.stream(
            "POST",
            CHAT_API_URL,
            headers=headers,
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
            if response.status_code >= 400:
                # Read the body INSIDE the stream context — touching .text on
                # an unread streaming response raises ResponseNotRead (the old
                # crash that killed the reply mid-stream).
                body = (await response.aread()).decode("utf-8", errors="replace")
                warning(
                    f"chat_stream: HTTP {response.status_code} from router: {body[:200]}"
                )
                yield error_frame(
                    f"The model router returned HTTP {response.status_code}."
                )
                return
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]  # strip "data: "
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    content = choices[0].get("delta", {}).get("content")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue
    except (httpx.ConnectError, httpx.TimeoutException):
        # ConnectTimeout is a TimeoutException, NOT a ConnectError — without
        # this it fell through to the generic branch (Wave 1 #1 "also").
        warning("chat_stream: cannot reach model router (connect/timeout)")
        yield error_frame("I can't reach the model router right now.")
    except Exception as e:
        warning(f"chat_stream: {type(e).__name__}: {e}")
        yield error_frame(f"Something broke mid-reply ({type(e).__name__}).")


async def nim_complete(
    system: str,
    user: str,
    client: httpx.AsyncClient,
    model: str = CHAT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 512,
) -> str | None:
    """Non-streaming completion for reflection/synthesis (future phase)."""
    try:
        headers = {}
        if NVIDIA_API_KEY and NVIDIA_API_KEY.strip():
            headers["Authorization"] = f"Bearer {NVIDIA_API_KEY.strip()}"

        response = await client.post(
            CHAT_API_URL,
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                # Omniroute defaults to streaming when omitted
                "stream": False,
            },
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=5.0),
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("choices", [{}])[0].get("message", {})
        content = message.get("content") or ""
        # Omniroute quirk (verified live): when the model hits max_tokens still
        # in its reasoning phase, content is a verbatim copy of the reasoning
        # field — pure meta-analysis, no answer. Treat that as a failed
        # completion rather than letting it pollute reflections/extraction.
        reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
        if reasoning and content.strip() == reasoning.strip():
            warning("nim_complete: truncated mid-reasoning (content==reasoning), dropping")
            return None
        # Some models leak <think> preamble into content instead — strip it.
        return strip_reasoning(content) or None
    except Exception:
        return None


if __name__ == "__main__":
    assert strip_reasoning("<think>blah</think>Hello") == "Hello"
    assert strip_reasoning("truncated reasoning tail</think>Real answer") == "Real answer"
    assert strip_reasoning("plain answer") == "plain answer"
    assert strip_reasoning("<think>only reasoning, no close") == "<think>only reasoning, no close"
    frame = error_frame("x")
    assert frame.startswith("\x1e") and frame.endswith("\x1e")
    assert json.loads(frame.strip("\x1e"))["event"] == "error"
    print("OK engine self-checks")
