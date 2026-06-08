from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.directive import get_directive
from models.inference.engine import chat_stream
from models.prompts.templates import build_chat_messages
from backend.session import session

router = APIRouter()


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


@router.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """Chat endpoint using NVIDIA NIM."""
    # Get directive (fails fast if missing/empty)
    directive = get_directive()

    # Build messages with history
    messages = build_chat_messages(directive, request.message, session.history())

    # Stream and accumulate
    full_reply = ""
    # Note: we'll inject the client via app.state in app.py
    from backend.app import http_client
    async for token in chat_stream(messages, http_client):
        full_reply += token

    # Append turns to session
    session.append("user", request.message)
    session.append("assistant", full_reply)

    return ChatResponse(reply=full_reply)