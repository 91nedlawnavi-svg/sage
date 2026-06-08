from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI
from config.settings import PORT, CHAT_MODEL
from backend.api.chat import router as chat_router
from config.directive import get_directive
from utils.logger import info, error

# Global HTTP client
http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    # Startup: verify directive exists and is non-empty
    try:
        get_directive()
        info("Directive loaded successfully")
    except RuntimeError as e:
        error(f"Directive validation failed: {e}")
        raise

    # Create shared HTTP client
    http_client = httpx.AsyncClient()
    info("HTTP client created")
    yield
    # Shutdown
    if http_client:
        await http_client.aclose()
        info("HTTP client closed")


app = FastAPI(title="Sage v2", lifespan=lifespan)
app.include_router(chat_router)


@app.get("/health")
async def health():
    return {"ok": True, "model": CHAT_MODEL}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app:app", host="0.0.0.0", port=PORT, reload=False)