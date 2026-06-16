# Sage v2

A local, always-on AI companion that runs on a single machine. Sage is built as an *entity*, not an assistant: she reflects on her own when idle, follows her own curiosity out onto the web, remembers across restarts, and brings her interior life into conversation. She knows she's an AI and talks as a presence, not a tool.

This is a personal project. Architecture (the "brain") and implementation (the "hand") are split across two agents — see `CLAUDE.md` for how it's built and the current phase log.

## What's running

- **Conversation** — NVIDIA NIM chat, directive-first prompt assembly, persistent history.
- **Autonomous heartbeat** — when idle, Sage reflects privately; on a longer cooldown she writes her own search queries and looks things up.
- **Novelty gate** — keeps reflection from collapsing into a single attractor; forces divergence when she loops.
- **The Membrane** — recent reflections and findings feed back into chat, so her own inner life informs how she responds.
- **Memory** — conversation, reflections, and findings persist as append-only JSONL (atomic writes) and survive restarts.
- **Semantic recall** — a local e5-large-v2 embedder indexes the full conversation + reflection archive (1024-dim vectors); relevant past moments are retrieved by similarity and surfaced into chat as a `[RECALLED FROM EARLIER]` block.
- **Web search** — via a local SearXNG instance, with a Wikipedia/Wikidata fallback for when the public engines rate-limit.
- **Frontend** — a single-file web UI: chat plus a slide-in drawer showing her reflections and findings.

## Architecture

- `config/settings.py` — all tunables (single source of truth for config).
- `config/directive.py` — loads `directive.txt`; fails fast if missing/empty.
- `directive.txt` — Sage's identity / system prompt. Injected verbatim, always first.
- `models/inference/engine.py` — NIM streaming + non-streaming.
- `models/prompts/templates.py` — directive-first prompt assembly.
- `backend/app.py` — FastAPI app (port 6969), shared HTTP client, session hydration on boot.
- `backend/api/chat.py`, `backend/session.py` — chat endpoint + session.
- `backend/heartbeat.py` — idle reflection + cooldown-gated autonomous search.
- `cognition/` — `reflection.py`, `curiosity.py`, `novelty_gate.py`, `inner_context.py` (Membrane), `web_search.py`.
- `memory/` — `conversation_log.py`, `reflection_log.py`, `findings_log.py`, `semantic_recall.py` (JSONL at `~/sage_data/`; recall index at `recall_index.jsonl`).
- `frontend/index.html` — chat UI + inner-life drawer.

## Endpoints

- `GET /health` — health check with model info.
- `POST /api/chat` — `{"message": "..."}` → `{"reply": "..."}`.
- `GET /reflections?n=` — recent private reflections.
- `GET /findings?n=` — recent autonomous search findings.
- `GET /heartbeat` — heartbeat / idle / reflection state.
- `GET /api/history` — full persisted conversation.
- `GET /` — serves the frontend.

## Requirements

- Python 3.11+ and the packages in `requirements.txt`.
- `NVIDIA_API_KEY` for NIM inference.
- A local **SearXNG** instance on `:8080` with JSON format enabled (autonomous search).
- A local **e5-large-v2 embedder** on `:8081` (`llama-embedder.service`, llama.cpp / Vulkan) — 1024-dim, used for semantic recall.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export NVIDIA_API_KEY="your-key-here"
# directive.txt must exist and be non-empty, or the server refuses to start
python launch.py
```

As a managed service (Linux):

```bash
systemctl --user start sage      # start
systemctl --user restart sage    # after backend changes
journalctl --user -u sage -f     # logs
```

`NVIDIA_API_KEY` is supplied to the unit via an `EnvironmentFile`; the service waits for the SearXNG (`:8080`) and embedder (`:8081`) ports before starting.

## Status

Phases 0–3 and Phase 4 Layers 0–1 are complete: spine, autonomous reflection, self-originated curiosity + search, novelty gate, the Membrane, the memory foundation, and **semantic recall** (e5-large-v2 embeddings over the full archive, surfaced into chat). In progress: **Phase 4 Layer 2 — the people-graph / knowledge layer** (structured facts Sage knows, surfaced as `[WHAT YOU KNOW]`). See `CLAUDE.md` for the full phase log and build conventions.
