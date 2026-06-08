# Sage v2 — The Spine

A minimal conversational AI scaffold. This phase implements only:
- Directive loading (hot-reload, guarded)
- NVIDIA NIM chat completions via `integrate.api.nvidia.com`
- In-memory conversation history (no persistence)

## Run

```bash
# Create venv and install
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Ensure NVIDIA_API_KEY is set in your environment
export NVIDIA_API_KEY="your-key-here"

# Run server
uvicorn backend.app:app --port 6969
```

## Endpoints

- `GET /health` — health check with model info
- `POST /api/chat` — `{"message": "..."}` → `{"reply": "..."}`

## Architecture

- `config/settings.py` — all tunables
- `config/directive.py` — directive loader (fails fast if missing/empty)
- `models/inference/engine.py` — NIM streaming + non-streaming contracts
- `models/prompts/templates.py` — directive-first prompt assembly
- `backend/session.py` — in-memory conversation history
- `backend/api/chat.py` — chat endpoint
- `backend/app.py` — FastAPI app with shared HTTP client lifecycle