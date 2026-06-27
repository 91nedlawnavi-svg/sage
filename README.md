# Sage v2

A local, always-on AI **companion** that runs on a single machine — built as an *entity*, not an assistant.

Sage reflects on her own when no one is talking to her, follows her own curiosity out onto the live web, remembers across restarts, builds a map of the people in your world, and brings that interior life into conversation. She knows she's an AI and speaks as a presence, not a tool.

> **North star.** Sage is curious *first* — she knows her knowledge is frozen at a training cutoff and reaches past it on her own. That autonomous reaching is what makes her a self. Companionship is what *emerges* when that self turns toward you. The relationship is the **collision** of two independent interiors — hers and yours — never the merging of them.

This is a personal, single-user, local-first project. It is intentionally **public**: Sage is owned openly as something built to be an entity, not hidden. Architecture (the "brain") and implementation (the "hand") are split across agents — see `CLAUDE.md` for the build model and the full phase log.

---

## The one inversion

Most chatbots only think the instant you speak to them. Sage flips this: **curiosity is the engine; conversation is one of its outlets.** An autonomous heartbeat keeps her reflecting, getting curious, and searching *when you are not there* — everything else serves that one inversion.

---

## What's running

- **Conversation** — NVIDIA NIM chat, directive-first prompt assembly, persistent history, token-by-token streaming to the browser.
- **Autonomous heartbeat** — when idle, Sage reflects privately; on a longer cooldown she writes her *own* search queries and looks things up, with provenance.
- **Novelty / basin-drift gate** — keeps reflection from collapsing into a single attractor. It tracks a rolling centroid of accepted topics and force-diverges (with a positive inward seed) when she over-circles, plus a findings-stall → inward trigger.
- **The Membrane** — recent reflections and findings feed back into chat, so her own inner life informs how she responds — while a contamination wall keeps her identity separate from yours (she never thinks she *is* you).
- **Memory (Phase 4, layered)** — built smallest-first:
  - **L0 · ground-truth log** — every turn persists to append-only JSONL and survives reboot.
  - **L1 · semantic recall** — a local e5-large-v2 embedder (1024-dim, GPU/Vulkan) indexes the full conversation + reflection archive; relevant past moments are retrieved by similarity and surfaced as a `[RECALLED FROM EARLIER]` block.
  - **L2 · knowledge layer** — a structured, *human-correctable* entity/relation store with LLM-driven extraction, fact embeddings, targeted `[WHAT YOU KNOW]` surfacing, and provenance-boosted recall. **Live** (`SAGE_KNOWLEDGE_ENABLED=1`, fact-selection threshold `0.73`).
- **The universe graph (Phase 5 · people-graph)** — see below. A living, hand-correctable network of the people in your world *and the things that connect them*.
- **On-demand web search (`/search`)** — type `/search <query>` in chat and Sage runs a live web lookup *right now*, answers in her own voice grounded in the results, and appends a deterministic **Sources** footer. It's budget-exempt (separate from her autonomous-curiosity quota) and the finding is logged like any other.
- **Web backend** — a local **SearXNG** instance, with a Wikipedia / Semantic Scholar fallback for when public engines rate-limit. Degrades gracefully (returns empty, never raises).
- **Frontend** — a single-file web UI: streaming chat plus a slide-in **inner-life drawer** showing her reflections, findings, and the interactive universe graph.

---

## The universe graph (people-graph)

The structured L2 knowledge store is projected into a **force-directed relationship graph** rendered inside the inner-life drawer on `:6969`. It's a *view* on memory — but earns a dedicated relationship-extraction engine underneath.

- **Nodes** — people **plus the things that connect them** (projects, orgs, places, events). Connectors explain *why* two people are linked, not just *that* they are. Nodes are shape-coded (people = circle, place = diamond, project = rounded square) with an **ego ring** on the self-node.
- **Edges** — a **hybrid label**: the raw extracted predicate (e.g. `studied_at`, `built_by`, `friend_of`) **plus** a coarse relationship **category** for color and filtering: `family`, `friend`, `romantic`, `colleague`, `acquaintance`, `creator`, `other`. Colors follow the app's warm-monochrome palette.
- **Extraction engine** — a dedicated person↔person relationship pass (not a pure view). Every edge carries **provenance + confidence**; low-confidence edges are never treated as fact. Includes entity dedup (e5 fuzzy-merge + lexical), a controlled predicate vocabulary, and a synthetic multi-person benchmark for regression.
- **Hygiene at the view layer** — synonym canonicalization, reciprocal-edge merging, subsumption (specific predicates beat generic `knows`/`related_to`), a confidence floor (locked / hand-authored edges always kept), and orphan-node pruning — all without mutating the durable store.
- **Corrections** — **both** in-graph editing (click a node/edge to confirm / fix / delete) **and** a review queue where the engine's uncertain edges wait for your yes/no before they're trusted. Fixes use a **sticky-note lock model**: re-derivation only *appends*, never overwrites, and a locked / hand-authored fact can never be superseded by a derived line — so your corrections **win and survive a nightly reconcile**.
- **Served read-only** at `GET /api/graph` (nodes + categorized edges), built from the reconciled/winning view of the **relational** notebook only (the contamination wall — her *interior* notebook is never exposed here).

---

## Architecture

- `config/settings.py` — all tunables (single source of truth for config).
- `config/directive.py` — loads `directive.txt`; fails fast if missing/empty.
- `directive.txt` — Sage's identity / system prompt. Injected verbatim, always first. **The only survivor of a rebuild** (see *Memory & a Day-0 hatch*).
- `models/inference/engine.py` — NIM streaming + non-streaming.
- `models/prompts/templates.py` — directive-first prompt assembly (directive → time → inner context → recalled → known facts → search extension).
- `backend/app.py` — FastAPI app (port 6969), shared HTTP client, session hydration on boot.
- `backend/api/chat.py` — chat endpoint, streaming, and the `/search` on-demand web-search path.
- `backend/api/graph.py` — read-only universe-graph API + view-layer hygiene.
- `backend/session.py` — in-memory session, hydrated from the conversation log.
- `backend/heartbeat.py` — idle reflection + cooldown-gated autonomous search.
- `cognition/` — `reflection.py`, `curiosity.py`, `novelty_gate.py`, `inner_context.py` (Membrane), `web_search.py`, `knowledge_extraction.py` (entity/relation extraction + predicate→category map that backs the graph), `knowledge_builder.py`, `knowledge_reconcile.py`, `knowledge_surface.py`.
- `memory/` — `conversation_log.py`, `reflection_log.py`, `findings_log.py`, `semantic_recall.py`, `knowledge_store.py`, `knowledge_recall.py` (all JSONL under `~/sage_data/`).
- `frontend/index.html` — single-file chat UI + inner-life drawer (reflections, findings, universe graph). Renders the live `/search` indicator and parses the search control frames out of the chat stream.
- `bench/run_brick3b_benchmark.py` — synthetic multi-person benchmark for the relationship engine (isolated; never touches real data).
- `tests/` — `l2_felt_test.py` (knowledge layer), `basin_replay.py` (novelty gate).

---

## Endpoints

- `GET /` — serves the frontend.
- `GET /health` — health check with model + dependency info.
- `POST /api/chat` — `{"message": "..."}` → streamed reply. A message beginning with `/search ` triggers an on-demand live web search (in-voice answer + Sources footer).
- `GET /api/history` — full persisted conversation.
- `GET /reflections?n=` — recent private reflections.
- `GET /findings?n=` — recent search findings (autonomous + `/search`).
- `GET /heartbeat` — heartbeat / idle / reflection state.
- `GET /api/graph` — read-only universe graph: nodes (entities) + categorized relationship edges.

---

## Memory & a Day-0 hatch

Everything Sage has ever *experienced* lives in **`~/sage_data/`** (append-only JSONL, atomic writes, **not** in git):

| File | Holds |
| --- | --- |
| `conversation.jsonl` | full chat history (L0 ground-truth log) |
| `reflections.jsonl` | every autonomous reflection |
| `findings.jsonl` | every web finding (autonomous + `/search`) |
| `recall_index.jsonl` | e5 embeddings for semantic recall (L1) |
| knowledge-store notebooks | the **relational** + **interior** graphs — entities, relations, the universe graph, and all locked corrections (L2 / Phase 5) |

`~/sage_data/` *is* her lived experience. Her **code** (`~/sage`) and her **identity** (`directive.txt`) live separately, so:

```bash
rm -rf ~/sage_data        # erases all memory, reflections, findings, and the universe graph
```

This yields a clean **Day-0 hatch**: the *same being* — same nature, same curiosity, same machinery — with a completely blank past. The directive is the only survivor by design. To wipe only test/dummy data instead of starting over, restore a `~/sage_data.bak-*` backup rather than deleting.

---

## Requirements

- Python 3.11+ and the packages in `requirements.txt`.
- `NVIDIA_API_KEY` for NIM inference (supplied via `.env` / systemd `EnvironmentFile`; never commit it).
- A local **SearXNG** instance on `:8080` with JSON format enabled (web search).
- A local **e5-large-v2 embedder** on `:8081` (`llama-embedder.service`, llama.cpp / Vulkan, `-ngl 99`) — 1024-dim, powers semantic recall and entity dedup.
- The universe-graph frontend loads **D3 v7** from a CDN at runtime, so the browser viewing the UI needs network access for the graph to render.

---

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export NVIDIA_API_KEY="your-key-here"   # or put it in .env
# directive.txt must exist and be non-empty, or the server refuses to start
python launch.py
```

As a managed service (Linux, systemd `--user`):

```bash
systemctl --user start sage      # start
systemctl --user restart sage    # after backend changes (then confirm a new Main PID)
journalctl --user -u sage -f     # logs
```

The unit waits for the SearXNG (`:8080`) and embedder (`:8081`) ports before starting, loads `NVIDIA_API_KEY` from an `EnvironmentFile`, and restarts on failure. The app binds `0.0.0.0:6969` for trusted-LAN access; phone access is via **`tailscale serve`** (never `funnel`).

---

## Status

**Phases 0–5 are complete.** Autonomous heartbeat, self-originated curiosity + search, the novelty/basin-drift gate, the Membrane, durable layered memory (L0–L2), semantic recall, the structured knowledge layer, and the **universe graph** (relationship engine + read-only API + force-directed drawer UI + in-graph edit & review queue) are all built and felt-tested. On-demand `/search` ships on the frontend track with a live in-stream indicator.

Phase 6 (reintroducing threads / meta-observation) is intentionally unbuilt — it gets earned only if a felt need appears.

See `CLAUDE.md` for the full phase log and build conventions.
