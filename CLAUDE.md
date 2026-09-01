# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- **Run**: `python3 launch.py` — starts web server on port 6969 + heartbeat thread
- **Tests**: `python3 -m pytest tests/` or `python3 -m unittest tests.test_foundation`
- **Single test**: `python3 -m pytest tests/test_foundation.py -k test_name`
- **Model audition**: `python3 tools/model_audition.py <alias> [--output workbench/audition.json]`
- No lint config, Makefile, or package manager. Stdlib-only Python.

## Architecture

Sage is a single-user personal intelligence with persistent episodic memory. Python 3, no external dependencies.

**Entry point**: `launch.py` loads `.env`, wires stores/router/heartbeat, starts `SageServer`.

**Core flow**: User message → `web.py:_chat()` → `sage.py:accept_message()` → `EventStore.append()` → router stream → reply persisted as assistant event. Heartbeat runs background entity extraction and reflection passes.

**Key modules** (all in `src/`):
- `sage.py` — message handling, directive loading, router message building
- `web.py` — HTTP server (`ThreadingHTTPServer`), chat API, streaming NDJSON replies
- `events.py` — append-only JSONL event store with hybrid recall (BM25 + cosine similarity)
- `database.py` — SQLite mirror layer: separate relational (`~/sage_data/relational/relational.db`) and interior (`~/sage_data/interior/interior.db`) databases, dual-written alongside JSONL. JSONL remains source of truth; mirrors are derived and rebuildable via `tools/backfill_sqlite.py`
- `router.py` — LLM router client with model fallback chain
- `heartbeat.py` — background thread for entity extraction and reflection
- `interior.py` — private storage (reflections, waiting messages). Beliefs are computed at recall, never stored
- `sensitive.py` — privacy classification logic
- `search.py` — web search integration

**Frontend**: Static HTML/CSS/JS in `src/static/`.

**Dual storage**: JSONL files under `~/sage_data/` are the source of truth. SQLite mirrors (`relational/relational.db`, `interior/interior.db`) are dual-written alongside JSONL for indexed reads and can be rebuilt from JSONL via `tools/backfill_sqlite.py`. `EventStore` and `InteriorStore` accept an optional `mirror` parameter; mirror failures are logged but never lose a turn.

**Config**: `.env.example` shows required vars (`SAGE_CHAT_MODELS`, optional `SAGE_EXTRACT_MODEL`, `PORT`). Systemd user unit at `~/.config/systemd/user/sage.service`, with a drop-in dir `sage.service.d/` that also sets env — check both.

## Authority hierarchy

1. `docs/NORTH_STAR.md` — purpose and felt outcome
2. `docs/INVARIANTS.md` — permanent constraints
3. `docs/DECISIONS.md` — settled decisions
4. `docs/BLUEPRINT.md` — behavior map (not implementation claim)
5. `docs/MILESTONE.md` — current work and acceptance evidence
6. `README.md` — present reality

Older V3 commits are implementation history, not current authority.

## Boundaries

- Lived memory is `~/sage_data/`; identity, code, project records stay outside it
- Sensitive material never enters casual recall, embeddings, or background provider prompts
- Original events are not replaced by frozen facts or current-state tables
- No writes to lived memory, `.env`, credentials, service restarts, or destructive migrations without explicit approval
- No speculative abstractions or unrelated refactors

</content>