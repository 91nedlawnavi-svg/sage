from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.routing import APIRouter
from pydantic import BaseModel
from pathlib import Path
from config.settings import PORT, CHAT_MODEL, TIMELAPSE, HEARTBEAT_INTERVAL_SECONDS, AUTONOMOUS_SEARCH_COOLDOWN_SECONDS, AUTONOMOUS_SEARCH_MAX_PER_DAY, MEMORY_CORE_SQLITE
from backend.api.chat import router as chat_router
from backend.api.graph import router as graph_router
from backend.api.desk import router as desk_router
from backend.api.voice import router as voice_router
from backend.heartbeat import Heartbeat
from config.directive import get_directive
from utils.logger import info, error
from memory.reflection_log import read_recent
from memory.findings_log import read_recent as read_recent_findings
from memory.conversation_log import load_all
from memory import semantic_recall
from backend.session import session

# Frontend static file serving
FRONTEND = Path(__file__).parent.parent / "frontend"

# Global HTTP client
http_client: httpx.AsyncClient | None = None
# Global heartbeat instance
heartbeat: Heartbeat | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client, heartbeat
    # Startup: verify directive exists and non-empty
    try:
        get_directive()
        info("Directive loaded successfully")
    except RuntimeError as e:
        error(f"Directive validation failed: {e}")
        raise

    if TIMELAPSE:
        info("TIME-LAPSE MODE ACTIVE",
             interval=HEARTBEAT_INTERVAL_SECONDS,
             search_cooldown=AUTONOMOUS_SEARCH_COOLDOWN_SECONDS,
             search_budget=AUTONOMOUS_SEARCH_MAX_PER_DAY)

    # Wave 2 memory core: apply any pending schema migrations before anything
    # touches the stores (writers also ensure lazily; this front-loads it).
    if MEMORY_CORE_SQLITE:
        try:
            from memory.sqlite_core import ensure_schema
            ensure_schema("relational")
            ensure_schema("interior")
            info("Memory core (SQLite) schema ready")
        except Exception as e:
            error(f"Memory core schema check failed: {e}")

    # Hydrate conversation history from disk (Phase 4 Layer 0)
    try:
        history = load_all()
        session.replace_history(history)
        info(f"Conversation history loaded: {len(history)} turns")
    except Exception as e:
        error(f"Failed to load conversation history: {e}")

    # Create shared HTTP client
    http_client = httpx.AsyncClient()
    info("HTTP client created")

    # Phase 4 Layer 1: warm the semantic-recall index with one throttled batch;
    # the heartbeat drains the rest of the backlog over subsequent beats.
    try:
        indexed = await semantic_recall.reindex(http_client)
        info(f"Semantic recall index warmed: +{indexed} this pass")
    except Exception as e:
        error(f"Semantic recall warm-up failed: {e}")

    # Start heartbeat
    heartbeat = Heartbeat(http_client)
    heartbeat.start()

    yield

    # Shutdown
    if heartbeat:
        heartbeat.stop()
        await heartbeat.aclose()
        heartbeat = None
    if http_client:
        await http_client.aclose()
        info("HTTP client closed")
        http_client = None


app = FastAPI(title="Sage v2", lifespan=lifespan)
app.include_router(chat_router)
app.include_router(graph_router)
app.include_router(desk_router)
app.include_router(voice_router)

# Frontend assets (app.css, app.js, graph.js) split out of index.html so a
# stray tag breaks one file, not the whole UI. "/" still serves the HTML.
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/health")
async def health():
    return {"ok": True, "model": CHAT_MODEL}


@app.get("/reflections")
async def get_reflections(n: int = 20):
    """Return the most recent N private reflections."""
    return {"reflections": read_recent(n)}


@app.get("/findings")
async def get_findings(n: int = 20):
    """Return the most recent N web search findings."""
    return {"findings": read_recent_findings(n)}


@app.get("/api/history")
async def get_history():
    """Return full chat history for UI rehydration on page load (Phase 4 L0).

    When SQLite core is on:
    - Annotates each message with held_close flag (§2.8 quiet dot)
    - Prepends any unread waiting message as a leading assistant turn (§3.4 reach)
    """
    messages = load_all()
    if MEMORY_CORE_SQLITE:
        try:
            from memory.relational_api import held_close_source_keys, get_waiting_message
            hc = held_close_source_keys()
            if hc:
                for m in messages:
                    if m.get("id") in hc:
                        m["held_close"] = True
            wm = get_waiting_message()
            if wm:
                messages = [{
                    "id": "waiting_message",
                    "role": "assistant",
                    "content": wm["content"],
                    "ts": wm.get("written_ts") or wm.get("revised_ts"),
                    "kind": "waiting",
                }] + messages
        except Exception:
            pass
    return {"messages": messages}


class HeldCloseRequest(BaseModel):
    held: bool


@app.post("/api/episodes/{source_key}/held-close")
async def toggle_held_close(source_key: str, req: HeldCloseRequest):
    """Tap-toggle held-close for a conversation turn (§2.8 Elliot's override)."""
    if not MEMORY_CORE_SQLITE:
        return {"ok": False, "error": "SQLite core not enabled"}
    try:
        from memory.relational_api import set_held_close_by_source_key
        ok = await set_held_close_by_source_key(source_key, req.held, actor="elliot")
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/heartbeat")
async def get_heartbeat():
    """Return heartbeat status for observability."""
    if not heartbeat:
        return {"error": "Heartbeat not initialized"}
    return {
        "last_beat_ts": heartbeat.last_beat_ts,
        "last_reflection_ts": heartbeat.last_reflection_ts,
        "last_search_ts": heartbeat.last_search_ts,
        "searches_today": heartbeat.searches_today,
        "idle_seconds": session.idle_seconds(),
        "reflecting": heartbeat.reflecting,
    }


@app.get("/call")
async def call_ui():
    """Serve the voice call UI."""
    return FileResponse(FRONTEND / "call.html")


@app.get("/")
async def chat_ui():
    """Serve the chat UI."""
    return FileResponse(FRONTEND / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app:app", host="0.0.0.0", port=PORT, reload=False)
