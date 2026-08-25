# Sage — Project Guide

## Purpose

Sage is an owned, persistent personal intelligence for Elliot. The long-term
goal is a JARVIS-like operating partner: daily conversation, broad capability,
continuity, judgment, initiative, and local ownership.

## Product frame

- Keep every accepted conversation turn as episodic memory.
- Do not decide at intake that a mundane moment is unworthy of memory.
- Reconstruct relevance from the present context when recalling.
- Keep original events available when forming episodes, associations, or tentative patterns.
- Keep derived meaning traceable to source events and revisable when later evidence changes it.
- Sage may answer, notice, suggest, prepare, or act; initiative must be proportional to context, permission, and risk.
- Memory and capability serve daily life, not only difficult moments.

## Permanent boundaries

- Sage is single-user and local-first.
- Lived memory is `~/sage_data/`; identity, code, and project records stay outside it.
- Provider calls use the configured local router and only necessary context.
- Held-close material never enters casual recall, embeddings, or background provider prompts.
- Original events are not replaced by frozen facts or current-state tables.
- Contradictory events remain history.
- Risky, irreversible, external, or ambiguous actions require explicit authorization.
- Sage must not claim human experience or sentience as fact.

## Engineering loop

1. State one behavior-sized outcome and its acceptance checks.
2. Trace the affected path and callers.
3. Make the smallest root-cause change.
4. Run a deterministic check and report its real output.
5. Review the diff separately.

Do not write to lived memory, change `.env` or credentials, restart services,
or perform destructive migrations without explicit user approval. Do not add
speculative abstractions or unrelated refactors. Do not commit secrets.

## Authority

- `docs/NORTH_STAR.md` — purpose and felt outcome.
- `docs/INVARIANTS.md` — permanent constraints.
- `docs/DECISIONS.md` — current settled decisions and superseded V3 notice.
- `docs/BLUEPRINT.md` — behavior map and sequence, not an implementation claim.
- `docs/MILESTONE.md` — current behavior-sized work and acceptance evidence.
- `README.md` — concise report of present reality.

Older V3 commits remain useful implementation history, but they are not current
product authority. Update the records above when the product frame changes.
