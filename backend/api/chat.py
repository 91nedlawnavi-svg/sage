from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config.directive import get_directive
from models.inference.engine import chat_stream
from models.prompts.templates import build_chat_messages
from backend.session import session
from cognition.knowledge_surface import select_relevant_relations
from memory.conversation_log import append_message
from memory import semantic_recall, knowledge_recall

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
    user_message = request.message.strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    # Get directive (fails fast if missing/empty)
    directive = get_directive()

    from backend.app import http_client

    # Mark user activity before any recall / knowledge preparation. Those
    # steps can touch e5 and disk, and the heartbeat must see this request as
    # active while they run.
    session.begin_chat()

    try:
        # Embed the query ONCE — this single e5 call serves both recall and
        # knowledge surface, guaranteeing no second embed on the chat turn.
        _q_emb = await semantic_recall.embed_query(user_message, http_client)

        # Phase 4 Layer 2: targeted knowledge + semantic fact selection.
        # Reuses the query embedding; when _q_emb is None (e5 down, gate off,
        # cache empty) fact_vectors is also None and fallback is purely lexical.
        _fact_vectors = knowledge_recall.load_fact_vectors() if _q_emb else None
        knowledge_relations = select_relevant_relations(
            user_input=user_message,
            max_facts=12,
            query_embedding=_q_emb,
            fact_vectors=_fact_vectors,
        )
        _boost_keys = {
            k for rel in knowledge_relations for k in (rel.get("provenance") or [])
        } or None  # None = no boost (preserves old recall behaviour)

        # Phase 4 Layer 1: recall relevant older content by meaning.
        # Passes the pre-computed embedding so recall skips its own embed call.
        recall_block = await semantic_recall.recall(
            user_message,
            http_client,
            boost_keys=_boost_keys,
            query_embedding=_q_emb,
        )

        # Build messages with history (+ recalled long-term memory)
        # IMPORTANT: build messages uses session.history() which does NOT include
        # the current message yet — user_input=request.message is appended as the
        # final message by build_chat_messages itself. So we snapshot history before
        # persisting the current user message, avoiding duplication.
        messages = build_chat_messages(
            directive,
            user_message,
            session.history(),
            recall_block=recall_block,
            knowledge_relations=knowledge_relations,
        )

        # Persist user message BEFORE streaming so it survives assistant failure.
        # session.begin_chat() already updated the activity timestamp.
        append_message("user", user_message)
        session.append("user", user_message)
    except Exception:
        session.end_chat()
        raise

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
