from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config.directive import get_directive
from models.inference.engine import chat_stream
from models.prompts.templates import build_chat_messages
from backend.session import session
from memory.conversation_log import append_message, rotate_log

router = APIRouter()


class ChatRequest(BaseModel):
    message: str


@router.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    """Chat endpoint using NVIDIA NIM.

    Streams the reply to the client as plain-text token chunks so the UI can
    render progressively. The full reply is persisted to the conversation log
    and the in-memory session once the stream completes.
    """
    # Get directive (fails fast if missing/empty)
    directive = get_directive()

    # Build messages with history
    messages = build_chat_messages(directive, request.message, session.history())

    from backend.app import http_client

    async def token_stream():
        full_reply = ""
        async for token in chat_stream(messages, http_client):
            full_reply += token
            yield token

        # Persist only after a complete generation. If the client disconnects
        # mid-stream, GeneratorExit is raised here and we skip persisting a
        # half-delivered turn.
        if full_reply:
            # Mark user activity for heartbeat
            session.touch()
            # Persist to conversation log BEFORE appending to session
            # (so if session append fails, the log is already safe)
            append_message("user", request.message)
            append_message("assistant", full_reply)
            # Append turns to session
            session.append("user", request.message)
            session.append("assistant", full_reply)

    return StreamingResponse(
        token_stream(),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
