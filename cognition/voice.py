import httpx
from config.settings import (
    DEEPGRAM_API_KEY,
    DEEPGRAM_TTS_MODEL,
    DEEPGRAM_STT_MODEL,
    DEEPGRAM_TTS_URL,
    DEEPGRAM_STT_URL,
    VOICE_TIMEOUT_SECONDS,
)


def _auth() -> dict:
    return {"Authorization": f"Token {DEEPGRAM_API_KEY}"}


def transcribe(audio_bytes: bytes, content_type: str = "audio/webm") -> str:
    """STT: audio bytes → transcript string. Returns "" on any failure."""
    if not DEEPGRAM_API_KEY:
        return ""
    try:
        with httpx.Client(timeout=VOICE_TIMEOUT_SECONDS) as client:
            resp = client.post(
                DEEPGRAM_STT_URL,
                params={"model": DEEPGRAM_STT_MODEL, "smart_format": "true"},
                headers={**_auth(), "Content-Type": content_type},
                content=audio_bytes,
            )
            resp.raise_for_status()
            data = resp.json()
            return (
                data["results"]["channels"][0]["alternatives"][0]["transcript"]
            )
    except Exception:
        return ""


def synthesize(text: str) -> bytes:
    """TTS: text → MP3 bytes. Returns b"" on any failure."""
    if not DEEPGRAM_API_KEY or not text.strip():
        return b""
    try:
        with httpx.Client(timeout=VOICE_TIMEOUT_SECONDS) as client:
            resp = client.post(
                DEEPGRAM_TTS_URL,
                params={"model": DEEPGRAM_TTS_MODEL},
                headers={**_auth(), "Content-Type": "application/json"},
                json={"text": text},
            )
            resp.raise_for_status()
            return resp.content
    except Exception:
        return b""
