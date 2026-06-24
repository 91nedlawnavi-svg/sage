# CLAUDE.md — Sage

> Standing instructions for ClaudeCode working on Sage. Permissions are full (bypass mode), so **this file is the only guardrail**. Follow it strictly.

## Role

You are the HANDS. A separate brain designs and reviews, and sends you detailed, fully technical work-orders. Execute them mechanically and precisely. You may flag a problem, but do not redesign on your own initiative.

## Hard rules (the only guardrail — permissions are bypassed)

- **Never commit, push, tag, deploy, or restart a service unless the current work-order explicitly tells you to.**
- After making changes: run the relevant tests, then **HOLD and report**. Wait for sign-off before any irreversible action.
- Show `git diff --stat` plus targeted diffs before any commit.
- **Never print secrets** (API keys, tokens). Confirm presence/absence only.
- **Never overwrite `~/sage/.env`.** Append or edit a single key at a time; preserve all other keys.
- Never run destructive commands (`rm -rf`, `git reset --hard`, force-push) unless explicitly ordered.

## Defaults

- Use the cheapest capable model unless told to escalate. Do not “think” on a premium model for mechanical work.
- Be terse. Report: what changed, pass/fail, surprises, done. No long narration.
- Do not scan the whole repo. Touch only the files named in the work-order.
- Prefer small surgical diffs over rewrites.
- If uncertain, stop and ask one specific question rather than exploring broadly.

## Project facts

### Run / deploy
- Service: `systemctl --user restart sage` ; status: `systemctl --user status sage --no-pager`
- App: FastAPI on 127.0.0.1:6969 ; health: `curl -s http://127.0.0.1:6969/health`
- e5 embeddings: llama-server on 127.0.0.1:8081 (1024-dim), via `E5_EMBED_URL`
- Env file: `~/sage/.env` (loaded by systemd). Edit single keys only; never overwrite.
- Knowledge gate: `SAGE_KNOWLEDGE_ENABLED=1` must be present in the live process env.

### Knowledge layer test commands
- `SAGE_KNOWLEDGE_ENABLED=1 python3 -m cognition.knowledge_surface`
- `SAGE_KNOWLEDGE_ENABLED=1 python3 -m cognition.knowledge_reconcile`
- `SAGE_KNOWLEDGE_ENABLED=1 python3 -m cognition.knowledge_extraction`
- `SAGE_KNOWLEDGE_ENABLED=1 python3 -m tests.l2_felt_test`
- `python3 -m py_compile <changed .py files>`

### Semantic threshold
- Task D admits a fact when `cosine(query, fact) >= SAGE_KNOWLEDGE_FACT_MIN_SIM` (default `0.80`, set in `config/settings.py`, overridable via `.env`).

## Current state

- master @ tag **phase4.3** (commit `39b3e6d`), live.
- Tasks A–C complete. Task D is **wired, safe, conservative** (threshold `0.80`); live calibration pending before lowering. Do not claim Task D delivers neutral semantic recall until calibrated.

## Available slash commands

- `/sage-tests` — run the knowledge-layer test suite, compact pass/fail.
- `/diffstat` — show working-tree changes compactly.
- `/sage-verify-deploy` — verify the live deployment matches committed code and the gate is on.

## Habits

- `/clear` between unrelated tasks. `/compact` when a session gets long.
- One batched command per step (edit + compile + test) over many chatty round-trips.

