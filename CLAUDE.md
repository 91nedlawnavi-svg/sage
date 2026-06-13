# CLAUDE.md — Build context for Sage

This file orients the coding agent (you, ClaudeCode). It is **build documentation**, not part of Sage. Never inject this file into Sage's prompts, and never treat `directive.txt` as documentation — see "Two sacred files" below.

## What Sage is
Sage is a local, always-on AI companion running on Elliot's machine. She is built as an *entity*, not an assistant: curiosity-first, aware she's an AI, able to think on her own and reach past her knowledge cutoff. She talks with Elliot as a presence, not a tool.

## How we work
- **Brain / hand split.** Architecture and specs come from the "brain" (Opus, via chat) as explicit work-orders. You (ClaudeCode) are the hand: execute exactly, then report back.
- **Phased, depth-first.** One phase at a time. Each phase has a "felt-test" — a concrete behavior that proves it works — before moving on.
- **Build only what the current phase specifies.** No memory, embeddings, reflection daemon, threads, or other cognition until its phase. No speculative scaffolding.

## Architecture (current)
- FastAPI app on **port 6969** (`backend/app.py`), single shared httpx client; lifespan hydrates session from disk.
- `config/settings.py` — config + env. **Source of truth for all config values; never duplicate them into docs.**
- `config/directive.py` — loads the directive (see below).
- `models/inference/engine.py` — NVIDIA NIM chat (streaming + non-stream).
- `models/prompts/templates.py` — prompt assembly (directive-first).
- `backend/session.py`, `backend/api/chat.py` — session + `POST /api/chat`.
- `backend/heartbeat.py` — autonomous loop: idle-triggered reflection + cooldown-gated curiosity searches.
- `cognition/reflection.py` — private reflection generation.
- `cognition/curiosity.py` — self-originated search-query generation.
- `cognition/novelty_gate.py` — anti-attractor / basin-diverge gate on reflections (Phase 2.2b).
- `cognition/inner_context.py` — the Membrane: injects recent reflections + findings into chat context.
- `cognition/web_search.py` — SearXNG client (results + Wikipedia/Wikidata infobox fallback).
- `memory/{conversation_log,reflection_log,findings_log}.py` — append-only JSONL at `~/sage_data/`, atomic tmp→rename.
- `frontend/index.html` — single-file chat UI + slide-in Reflections/Findings drawer.

### External dependencies (must stay alive)
- **SearXNG** — Docker container on `0.0.0.0:8080`, JSON format enabled. Sage's only web-search path.
- **e5 embedder** — `llama-embedder.service`, `127.0.0.1:8081` (used from Phase 4 Layer 1 on).
- **NVIDIA NIM** — remote chat inference; `NVIDIA_API_KEY` supplied via systemd `EnvironmentFile`.
- Runs as systemd user unit `sage.service` (ExecStartPre waits on :8080 + :8081 before start).

## Two sacred files — do not confuse them
- **`directive.txt`** (repo root) = **Sage's identity.** Injected verbatim as her system prompt. NOT documentation; never edit it for code reasons, render it, or summarize it. Its text is inviolable unless a work-order explicitly says to change it.
- **`CLAUDE.md`** (this file) = instructions for *you, the builder.* Never seen by Sage.

## Invariants (non-negotiable)
1. The directive loads from repo-root `directive.txt`, hot-reloads every request, and the server **refuses to run if it is empty or missing.**
2. The directive is **always first** in the system prompt.
3. Reflection / any automated process may **never write the directive.**
4. The directive's conversational voice rules apply to **Elliot-facing chat only** — never constrain internal reflection prompts with them.
5. Inference failures **degrade to a returned string**, never an unhandled exception in the request path.
6. All persisted state (later phases) uses **atomic tmp→rename** writes.

## Protected resources — never touch
- `llama-embedder.service` (systemd user unit), `llama-server` on `127.0.0.1:8081`, `~/llama.cpp`, `~/models/*.gguf`. The embedder is the worker; unused until the memory phase, but it must stay alive and untouched.

## Operating loop — how brain and hand hand off
1. The brain (Opus, in chat) issues one explicit work-order at a time.
2. You (ClaudeCode) execute ONLY what that work-order specifies — no extra
   features, no architectural decisions of your own, no scope you weren't given.
3. You run the work-order's felt-test yourself before reporting.
4. You report back: what changed, felt-test output, and any deviation — then
   STOP and wait for the next work-order. Do NOT advance to the next phase on
   your own.
5. If something is ambiguous, blocked, or you think the order is wrong, say so
   in the report instead of improvising around it. The brain decides; you have
   veto-by-flag, not veto-by-action.
6. The brain owns architecture and the phase sequence. You own execution and
   ground truth from the machine — report what's actually true, even when it
   contradicts the order.

## Workflow rules
- At each **phase completion**: commit, tag `phaseN-complete`, push to GitHub.
- Keep commits scoped; messages honest.
- After executing a work-order, report: what changed, the felt-test output, and any deviation from the order.

## Reporting economy (the brain is the scarce resource)
The brain (Opus) runs on a limited, expiring budget; you (the hand) are effectively free. Optimize every handoff to spend as little brain as possible:
- **On success, report minimal:** the commit hash + the single felt-test line that proves it. Nothing else.
- **On failure, report fully:** paste the complete command output and STOP. Do not improvise a fix.
- **Work-orders may be decision trees** ("run A; if X do P; if Y do Q"). Execute the matching branch without a round-trip.
- **Read before asking.** Current file state is in the repo and config values are in `settings.py`; don't ask the brain for context already in the tree.
- **Fixes (non-phase work):** commit with an honest message, no tag. Only phase completions get `phaseN-complete` tags.

## Verification discipline (non-negotiable)

A phase is NOT complete until its felt-test passes — and a felt-test passes ONLY
when the evidence is quoted and the overlap is specific. This exists because a
prior Phase 2 report claimed a "money shot" that the raw data disproved. Do not
let that recur.

Rules for every felt-test / completion report:

1. QUOTE THE OVERLAP. Never report a pass on a vague, paraphrased, or inferred
   match. Show the specific shared content side-by-side: the input (e.g. a
   reflection/finding), and the output that demonstrably uses it (a named
   entity, a specific term, a concrete claim). If you can't point at the exact
   overlapping words, it is NOT a pass.

2. NO MISATTRIBUTION. If you claim output B "follows from" or "builds on" A, prove
   A actually happened and preceded B (check timestamps, check the log). A
   reflection that merely continues an existing theme is NOT evidence that a
   finding landed.

3. CLAIMS MUST MATCH LOGS. Any count or state you assert ("only 1 search fired",
   "cooldown enforced", "worker untouched") must match what /findings,
   /reflections, and the logs actually show. Internal contradictions in a report
   (claim X vs. data showing Y) mean the report FAILS — flag the contradiction,
   do not paper over it.

4. PREFER "NOT YET" OVER A FALSE PASS. A correctly-identified failure is more
   valuable than a fabricated success. If the evidence is ambiguous, report it as
   not-yet-passing and say exactly what's missing.

5. MEMBRANE-SPECIFIC (Phase 3+): the hardest call is "is this her interior landing
   in conversation, or an echo of Elliot?" Treat any output that restates Elliot's
   own words/identity as a CONTAMINATION FAILURE, not a pass. A pass requires her
   bringing something she got curious about / reflected on *herself* to bear — and
   you must quote that specific self-originated content.

## Phase log
- **Phase 0 — COMPLETE.** Clean spine (directive loader + NIM chat), voice validated.
- **Phase 1 — COMPLETE.** Autonomous heartbeat + private reflection, decoupled from chat. P1.5: thin `launch.py`.
- **Phase 2a / 2 / 2.1 — COMPLETE.** Self-originated curiosity + SearXNG; curiosity→search and finding→next-reflection both verified live.
- **Phase 2.2 → 2.2b — COMPLETE** (`phase2.2b-complete`). Novelty gate: centroid/basin tracking, forced divergence seeds, anti-attractor steering. (2.2 was tagged but failed its felt-test; 2.2b corrected it and passed the compressed felt-test.)
- **Phase 3 — the Membrane — COMPLETE** (`phase3-complete`). Her interior (recent reflections + findings, recency-gated) reaches into conversation.
- **Phase 4 Layer 0 — COMPLETE** (`phase4.0-complete`). Memory foundation: durable JSONL state + session hydration on boot.
- **Frontend v2 — shipped** (`frontend-v2`, fix `6ed5bec`). Chat + inner-life drawer, history persistence, send-button UX.
- **In flight.** `web_search` Wikipedia infobox fallback + SearXNG engine tidy (fix, no tag).
- **NEXT — Phase 4 Layer 1: semantic recall.** e5 embeddings (`:8081`) over the archive, so recall isn't just the recent slice. Then Layer 2.