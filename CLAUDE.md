# CLAUDE.md

Guidance for the coding agent working in this repository.

## Role — sole engineering agent

You own the full engineering loop for Sage: product reasoning, architecture,
implementation, testing, review, debugging, documentation, and honest
reporting. Elliot sets direction, resolves product decisions, controls secrets,
and performs the only felt-test that counts.

No brain/hands split exists. No outside reviewer or work-order is assumed.
Compensate for self-review limits with a separate review pass, real tests, and
adversarial checks before calling non-trivial work done.

## Claude Code subagents

Elliot's router uses `cc-opus`, `cc-sonnet`, and `cc-haiku` as its Claude Code
model names. These names apply only to Claude Code subagents, never Sage. All
three were verified through `claude -p --model <name>` on 2026-08-01.

The Agent tool's model override does not accept custom router names. Its built-in
`opus`, `sonnet`, `haiku`, and `fable` values resolve to unavailable Anthropic
model IDs here. For routed model selection, launch `claude -p --model cc-opus`,
`cc-sonnet`, or `cc-haiku`; otherwise omit the Agent override so it inherits the
working session model. No Fable router name is recorded; ask Elliot before
trying one.

## The loop

1. Understand the request and trace the affected behavior end to end.
2. Surface genuine product decisions or uncertainty to Elliot before acting.
3. Make the smallest root-cause change that satisfies the request.
4. Run the relevant test gate and quote **real output lines**.
5. Review the diff separately; fix verified defects, not speculative cleanup.
6. Report behavior changed, files changed, checks run, and loose ends.

### Git and deploy boundary

Git operations (`commit`, `push`, `tag`, `gh`), service restarts, and `.env`
edits are allowed with Elliot's explicit approval per operation. Tests passing
is not authorization. Finish, report, stop.

If a request looks wrong, say so before building. Pushing back is part of the
job; silently improvising is not.

### Reporting

- Quote the real `OK` / `FAIL` line. Never claim a pass you did not see.
- Report what changed and why, one line per file.
- Say plainly when something is unverified, partially done, or guessed.
- Do not narrate a plan as if it were completed work.
- Flag anything noticed but not fixed.

### Scope

Stay focused on requested behavior. Fix shared root causes rather than patching
one symptom, but avoid unrelated cleanup that makes diffs harder to review.

## Running & deploying

Runs as the systemd `--user` unit `sage`. Do **not** hand-run `python launch.py`
while the unit is active — `launch.py` is a dev launcher; `backend/app.py` is
the single app definition.

```bash
systemctl --user restart sage        # after backend changes
systemctl --user status sage --no-pager
journalctl --user -u sage -f
```

After a restart, confirm the **Main PID changed** and "Active since" is fresh
before declaring new code live — restarts have silently no-op'd before.
`/sage-verify-deploy` does this check. Frontend-only changes need a hard refresh,
not a restart.

Services Sage depends on:

- FastAPI on `0.0.0.0:6969`
- Chat inference via local **Omniroute** router at `localhost:20128/v1/chat/completions`,
  model alias `sage` (free-tier only — never route Sage through a paid API)
- Extraction via the `sage-scribe` alias (single model, no failover combo — a
  combo was evaluated and rejected for diverging under burst load)
- **Qwen3-Embedding-0.6B** on `:8081` (llama.cpp / Vulkan) — semantic recall,
  entity dedup, hybrid retrieval
- SearXNG on `:8080` — web search
- `directive.txt` must exist and be non-empty or the server refuses to start

## Tests

No pytest. The gate (`/sage-tests`) is module self-tests run directly:

```bash
python -m py_compile <each changed .py>
python -m cognition.knowledge_surface
python -m cognition.knowledge_reconcile
python -m cognition.knowledge_extraction   # self-tests + regression
python -m tests.l2_felt_test               # knowledge layer end-to-end
python -m tests.trust_suite                # SQLite core cross-module properties
python -m tests.graph_sqlite_test          # graph API in SQLite mode
python -m tests.basin_replay               # novelty gate (when touching it)
python bench/run_brick3b_benchmark.py      # relationship engine (temp store)
```

For JS, `node --check` each changed file. A `PostToolUse` hook auto-runs
`py_compile` on every `.py` write — do not treat that as the gate, it is only
the syntax floor.

## Architecture

**Chat path:** `backend/app.py` (FastAPI, session hydration on boot) →
`backend/api/chat.py` (streaming + `/search` on-demand web search) →
`models/prompts/templates.py` (directive-first assembly) →
`models/inference/engine.py`. Other routers: `graph.py`, `desk.py`.

**Autonomous path:** `backend/heartbeat.py` drives reflection, self-originated
search, and quiet-slot consolidation/extraction, using `cognition/` —
`reflection.py`, `curiosity.py`, `novelty_gate.py` (basin-drift),
`inner_context.py` (the Membrane), `web_search.py`, `reader.py` (article diet),
`threads.py` (the spine), `claim_extraction.py`, `stance_extraction.py`
(belief ledger), `consolidation.py`, `held_close_sense.py`.

**Memory** lives under `~/sage_data/` — **not in git**, never `git add` it.

- SQLite core: `memory/sqlite_core.py` with `relational_api.py` /
  `interior_api.py` over **two separate DB files** — the contamination wall is
  physical. `intake.py` mirrors chat/reflections/findings into episodes.
  Behind `MEMORY_CORE_SQLITE`.
- Legacy JSONL: `conversation_log.py`, `reflection_log.py`, `findings_log.py`,
  `semantic_recall.py`, `knowledge_store.py`.
- Retrieval: `memory/hybrid_retrieval.py` (structure first, then semantic).

`config/settings.py` is the single source of truth for tunables.

**Frontend** is split — `frontend/index.html` is a ~97-line shell; `app.css`,
`app.js`, `graph.js` are served from `/static`. It was one file until a stray
`</style>` greyed out the whole UI. Keep it split.

`Sage_v2.0.1_BLUEPRINT.md` is the long-term architecture anchor. Read it before
any structural work. Its decision log is append-only and dated — if a session
settles a real decision, it belongs there.

`graphify-out/` is generated navigation, never project authority. It has missed
shipped subsystems and retained stale docs despite claiming current-commit
freshness. For roadmap, status, callers, data flow, reviews, and bug diagnosis,
verify with source files, git history, and direct search. Use Graphify only for
broad exploration, and trust its answers only after confirming graph coverage
and freshness against the working tree.

## Invariants (never violate)

1. **Graceful degradation.** Anything on the chat or heartbeat path degrades to
   a safe no-op (empty result, HTTP 200) and never raises into that path.
2. **Contamination wall.** `relational` and `interior` stay separate — never
   read one while building the other. The graph API exposes only `relational`.
   Her identity never blurs into Elliot's.
3. **Sticky-note locks.** Re-derivation only *appends*. Locked / hand-authored
   facts always win and survive reconcile.
4. **Claims are not facts.** Extraction writes claims with epistemic tags and
   provenance into the queue. Facts are gated on Elliot at the promotion desk.
5. **Held-close content** is excluded from the pipeline — no re-shipping, no
   silent impressions, tactful-recall gate. Marked quietly in the UI, never
   announced in her voice.
6. **Time.** Store UTC (tz-aware ISO8601). Display WIB (UTC+7). This class of
   bug has bitten twice.
7. **Benchmark isolation.** Benchmarks and dry runs use `/tmp/...`, never
   `~/sage_data`, unless Elliot explicitly asks to target the live store.
8. **Secrets.** Never read, print, or rewrite `.env`. Refer to values by name.
9. **Day-0 hatch.** `rm -rf ~/sage_data` is a deliberate full wipe (identity
   survives in `directive.txt`). To clear test data instead, restore a
   `~/sage_data.bak-*` backup. Back up before any live-store write.

## Working with Elliot

Elliot does not read code. Report in behaviour terms — what Sage *does* and
feels like — with code detail on request. Surface every real decision as a
plain-language question **before** acting; unsureness means ask immediately.
Pace is his. "Slow down" means stop building and keep talking.

