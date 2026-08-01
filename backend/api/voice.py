import asyncio
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from cognition.voice import transcribe, synthesize
from config.settings import VOICE_ENABLED

router = APIRouter()

MAX_UPLOAD_BYTES = 10_485_760  # 10 MB
ALLOWED_AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/wav", "audio/mp3",
                       "audio/mpeg", "audio/x-wav", "audio/x-mpeg"}


@router.get("/api/voice/status")
async def voice_status():
    return {"enabled": VOICE_ENABLED}


@router.post("/api/voice/stt")
async def stt(file: UploadFile = File(...)):
    """Audio blob (multipart) → transcript. Degrades to empty string on any failure."""
    if not VOICE_ENABLED:
        return {"transcript": ""}
    body = await file.read()
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Audio payload too large")
    content_type = file.content_type or "audio/webm"
    if content_type not in ALLOWED_AUDIO_TYPES:
        content_type = "audio/webm"  # default fallback, let Deepgram decide
    transcript = await asyncio.to_thread(transcribe, body, content_type)
    return {"transcript": transcript}


class TTSRequest(BaseModel):
    text: str


@router.post("/api/voice/tts")
async def tts(req: TTSRequest):
    """Text → MP3 bytes. Returns empty 200 on failure (UI stays silent)."""
    if not VOICE_ENABLED or not req.text.strip():
        return Response(content=b"", media_type="audio/mpeg")
    audio = await asyncio.to_thread(synthesize, req.text)
    return Response(content=audio, media_type="audio/mpeg")
