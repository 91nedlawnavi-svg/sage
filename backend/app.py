from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pathlib import Path
from config.settings import PORT, CHAT_MODEL, TIMELAPSE, HEARTBEAT_INTERVAL_SECONDS, AUTONOMOUS_SEARCH_COOLDOWN_SECONDS, AUTONOMOUS_SEARCH_MAX_PER_DAY
from backend.api.chat import router as chat_router
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
    """Return full chat history for UI rehydration on page load (Phase 4 L0)."""
    return {"messages": load_all()}


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


@app.get("/")
async def chat_ui():
    """Serve the chat UI."""
    return FileResponse(FRONTEND / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app:app", host="0.0.0.0", port=PORT, reload=False)
