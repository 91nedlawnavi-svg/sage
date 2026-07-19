# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Role

You are the sole agent for Sage — architecture, implementation, and review all yours. Sage is a local, always-on AI companion: an autonomous heartbeat keeps her reflecting and web-searching when idle; conversation is one outlet of that curiosity, not the trigger for it. See README.md for the full feature map and endpoint list.

## Claude Code subagents (model routing)

Elliot's router uses custom names for Claude models. When spawning subagents, these are the mappings:

- `cc-opus` = Opus
- `cc-sonnet` = Sonnet
- `cc-haiku` = Haiku

Policy: use the cheap tier (Haiku / `cc-haiku`) for mechanical work (file sweeps, log greps, batch checks), mid tier (Sonnet / `cc-sonnet`) for mid-weight implementation tasks; keep design, review, and judgment calls in the main model. This governs **Claude Code's own subagents only** — Sage's inference always goes through the free-tier Omniroute alias `sage`, never any Claude model (see Invariants).

## Running & deploying

The app runs as the systemd `--user` unit `sage` — do **not** hand-run `python launch.py` on the dev box while the unit is active.

```bash
systemctl --user restart sage        # after backend changes
systemctl --user status sage --no-pager
journalctl --user -u sage -f         # logs
```

After a restart, confirm the Main PID **changed** and "Active since" is fresh before declaring new code live — restarts have silently no-op'd before. `/sage-verify-deploy` does this check.

Local services Sage depends on:
- FastAPI app on `0.0.0.0:6969` (`backend/app.py` is the single app definition; `launch.py` is just a dev launcher)
- Chat inference via local Omniroute router at `localhost:20128/v1/chat/completions`, model alias `sage` (free-tier models only — never route Sage through paid Claude API; `NVIDIA_API_KEY` is optional legacy NIM)
- SearXNG on `:8080` (web search), e5-large-v2 embedder on `:8081` (semantic recall + entity dedup)
- `directive.txt` (Sage's identity prompt) must exist and be non-empty or the server refuses to start

## Tests

No pytest. The test gate (`/sage-tests`) is module self-tests run directly:

```bash
python -m py_compile <each changed .py>
python -m cognition.knowledge_surface
python -m cognition.knowledge_reconcile
python -m cognition.knowledge_extraction   # self-tests + regression
python -m tests.l2_felt_test               # knowledge-layer end-to-end
python -m tests.basin_replay               # novelty gate (when touching it)
python bench/run_brick3b_benchmark.py      # relationship engine (isolated, temp store)
```

Quote real output lines (OK/FAIL) when reporting results — never claim a pass without the line.

## Architecture

Request flow: `backend/app.py` (FastAPI, session hydration on boot) → `backend/api/chat.py` (streaming chat + `/search` on-demand web search) → `models/prompts/templates.py` (directive-first assembly: directive → time → inner context → recalled → known facts → search) → `models/inference/engine.py` (streaming + non-streaming completion).

Autonomous side: `backend/heartbeat.py` drives idle reflection and cooldown-gated self-originated search using `cognition/` (`reflection.py`, `curiosity.py`, `novelty_gate.py` — basin-drift gate, `web_search.py`). `cognition/inner_context.py` (the Membrane) feeds recent reflections/findings back into chat prompts.

Memory is layered under `~/sage_data/` (append-only JSONL, atomic writes, **not in git** — protected by `~/sage_data.bak-*` backups, never `git add` it):
- **L0** `memory/conversation_log.py` — ground-truth turn log, survives reboot
- **L1** `memory/semantic_recall.py` — e5 embedding index over conversation + reflections
- **L2** `memory/knowledge_store.py` + `cognition/knowledge_*.py` — structured entity/relation store with LLM extraction, reconcile, and surfacing; projected as the universe graph via `backend/api/graph.py` (read-only, view-layer hygiene only — never mutates the store)

`config/settings.py` is the single source of truth for tunables. `frontend/index.html` is the entire UI (single file, streaming chat + inner-life drawer + D3 graph).

## Invariants (never violate)

1. **Graceful degradation.** Code on the chat or heartbeat path must degrade to a safe no-op (return `[]`/empty dict, HTTP 200) and never raise into that path. Match existing contracts (semantic recall, membrane, web search all do this).
2. **Contamination wall.** The `relational` and `interior` knowledge notebooks stay separate — never read one while building the other; the graph API exposes only `relational`. Sage's identity must never blur into the user's.
3. **Sticky-note lock model.** Re-derivation of knowledge only *appends*; locked/hand-authored facts always win over derived lines and survive reconcile.
4. **Benchmark isolation.** Benchmarks and dry runs use a temp store (`/tmp/...`), never `~/sage_data`, unless explicitly targeting the live store.
5. **Secrets.** Never read, print, or rewrite `.env`; refer to its values by name only.
6. **Day-0 hatch.** `rm -rf ~/sage_data` is a deliberate full-memory wipe (identity survives in `directive.txt`); to clear test data instead, restore a `~/sage_data.bak-*` backup.

## Knowledge store API (quick reference)

`memory/knowledge_store.load_entities("relational")` / `load_relations("relational")`; relations carry object `{kind, value}`. Reconciled read-path lives in `cognition/knowledge_reconcile.py`.
