from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio
import re
import traceback

from config.directive import get_directive
from models.inference.engine import chat_stream
from models.prompts.templates import build_chat_messages
from backend.session import session
from cognition.knowledge_surface import select_relevant_relations
from memory.conversation_log import append_message
from memory import semantic_recall, knowledge_recall
from cognition.web_search import search
from memory.findings_log import append_finding

router = APIRouter()


def _build_search_block(query: str, results: list[dict]) -> str:
    """Build the search context block injected into the user message."""
    lines = [f'[WEB SEARCH RESULTS — requested by Elliot via /search: "{query}"]']
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        lines.append(f"{i}. {title} — {url}")
        lines.append(f"   {snippet}")
    lines.append("")
    lines.append("[Answer Elliot's request in your own voice. Synthesize across the results; don't dump them. Don't invent facts beyond them. Do not list the links yourself — they are appended automatically.]")
    return "\n".join(lines)


def _build_sources_footer(results: list[dict]) -> str:
    """Deterministic Sources footer — appended after the streamed prose."""
    seen = set()
    lines = ["\n\nSources:"]
    for r in results:
        url = r.get("url", "")
        title = r.get("title", "")
        if url and url not in seen:
            seen.add(url)
            lines.append(f"— {title} ({url})")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


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

    # ── /search command: on-demand web search (budget-exempt) ──────
    # Match "/search" only as a complete token (followed by whitespace or end of
    # message) so things like "/searching..." fall through to the normal path.
    if re.match(r"/search(\s|$)", user_message, re.IGNORECASE):
        query = user_message[len("/search"):].strip()
        if not query:
            msg = "What should I look into? Just tell me what you're curious about — a topic, a question, anything."
            try:
                append_message("user", user_message)
                session.append("user", user_message)
                append_message("assistant", msg)
                session.append("assistant", msg)
            finally:
                session.end_chat()
            return StreamingResponse(
                iter([msg]),
                media_type="text/plain; charset=utf-8",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        async def search_stream():
            full_reply = ""
            completed = False
            search_results = None
            try:
                # 1) Control frame: search_started (frontend shows indicator)
                yield '\x1e{"event":"search_started"}\x1e'

                # 2) Run search off the event loop (sync httpx client)
                results = await asyncio.to_thread(search, query)

                # 3) Control frame: search_done (frontend hides indicator)
                yield f'\x1e{{"event":"search_done","count":{len(results)}}}\x1e'

                if not results:
                    msg = "I came up empty this time. Try a different angle and I'll look again."
                    full_reply = msg
                    yield msg
                    completed = True
                    return

                # 4) Cap results + truncate snippets for token budget
                top = results[:5]
                for r in top:
                    s = r.get("snippet", "")
                    if len(s) > 500:
                        r["snippet"] = s[:500].rstrip() + "..."

                # 5) Build messages with search context
                search_block = _build_search_block(query, top)
                messages = build_chat_messages(
                    directive,
                    user_message,  # raw /search text — used for knowledge relevance
                    session.history(),
                )
                messages[-1]["content"] = search_block

                # 6) Stream the reply in Sage's voice
                async for token in chat_stream(messages, http_client):
                    full_reply += token
                    yield token

                # 7) Deterministic Sources footer (model never generates URLs)
                sources = _build_sources_footer(top)
                if sources:
                    full_reply += sources
                    yield sources

                search_results = results
                completed = True

            finally:
                # 8) Persist only a COMPLETE turn. Mirrors the normal path: if the
                #    client disconnects mid-stream, GeneratorExit fires at a yield,
                #    completed stays False, and we skip persisting a half-delivered
                #    reply (and never await during GeneratorExit cleanup).
                if full_reply and completed:
                    append_message("user", user_message)
                    session.append("user", user_message)
                    append_message("assistant", full_reply)
                    session.append("assistant", full_reply)

                    # 9) Write finding (best-effort, off-loop). Only on a real
                    #    result set. Failures are LOGGED, never silently swallowed,
                    #    so a bad call surfaces in journalctl instead of vanishing.
                    if search_results:
                        try:
                            await asyncio.to_thread(append_finding, query, search_results)
                        except Exception:
                            traceback.print_exc()

                session.end_chat()

        return StreamingResponse(
            search_stream(),
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Normal (non-/search) path ────────────────────────────────
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
