from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config.directive import get_directive
from models.inference.engine import chat_stream
from models.prompts.templates import build_chat_messages
from backend.session import session
from cognition.knowledge_surface import select_relevant_relations
from memory.conversation_log import append_message
from memory import semantic_recall

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

    from backend.app import http_client

    # Phase 4 Layer 2: compute boost keys from targeted knowledge facts so that
    # recall prefers source turns that actually generated a personal fact.
    _boost_rels = select_relevant_relations(user_input=request.message, max_facts=12)
    _boost_keys = {
        k for rel in _boost_rels for k in (rel.get("provenance") or [])
    } or None  # None = no boost (preserves old recall behaviour)

    # Phase 4 Layer 1: recall relevant older conversation + reflections by meaning
    recall_block = await semantic_recall.recall(request.message, http_client, boost_keys=_boost_keys)

    # Mark user activity NOW so heartbeat knows someone just interacted.
    # IMPORTANT: build messages uses session.history() which does NOT include
    # the current message yet — user_input=request.message is appended as the
    # final message by build_chat_messages itself. So we snapshot history before
    # persisting the current user message, avoiding duplication.
    session.begin_chat()

    # Build messages with history (+ recalled long-term memory)
    messages = build_chat_messages(
        directive, request.message, session.history(), recall_block=recall_block
    )

    # Persist user message BEFORE streaming so it survives assistant failure.
    # session.begin_chat() already updated the activity timestamp.
    append_message("user", request.message)
    session.append("user", request.message)

    async def token_stream():
        full_reply = ""
        completed = False
        try:
            async for token in chat_stream(messages, http_client):
                full_reply += token
                yield token
            completed = True
        finally:
            # Persist assistant only after a complete generation. If the client
            # disconnects mid-stream, GeneratorExit is raised inside the try
            # block, completed stays False, and we skip persisting a
            # half-delivered turn.
            if full_reply and completed:
                append_message("assistant", full_reply)
                session.append("assistant", full_reply)
            # Always decrement the active-chat counter, even on disconnect
            session.end_chat()

    return StreamingResponse(
        token_stream(),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
