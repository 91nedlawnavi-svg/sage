# Sage — Held Development Rules

## Purpose

Sage is Elliot's owned, persistent personal intelligence. The long-term goal
is a JARVIS-like operating partner: daily conversation, broad capability,
continuity, judgment, initiative, and local ownership.

## Working with Elliot

- Elliot does not understand code. Explain outcomes, reasons, and decisions in
  simple English. Translate technical work; do not require technical knowledge.
- Lead with what changed or what was learned. Keep updates brief and useful.
- Complete authorized work. Ask only when a missing answer or permission is
  necessary; honor authorization already given in the conversation.
- Keep investigations focused. Reuse verified context and avoid repeated reads
  or checks unless changes, failures, or uncertainty justify them.
- Use Medium reasoning effort by default. Before deep work likely to benefit
  from High, xHigh, or Ultra, tell Elliot which level to select and why, then
  wait for him to trigger it before starting that work.

## Hard rule: Caveman and Ponytail have separate jobs

- **Caveman full controls developer communication with Elliot.** Keep wording
  short, remove filler, and preserve meaning. Use plain English, not unexplained
  jargon. Expand when Elliot asks or brevity would make instructions unclear.
- **Ponytail full controls code implementation and review only.** Understand the
  affected flow, reuse existing code and built-in capabilities, and make the
  smallest correct change that fulfills the request. Avoid speculative features,
  unnecessary dependencies, abstractions, and unrelated refactors.
- Ponytail never controls conversation, explanations, questions, reports, or
  other prose. Ignore its code-first, reply-length, and output-format rules.
  Caveman owns communication; Ponytail owns coding choices.
- Caveman does not compress code or alter technical identifiers. Ponytail does
  not remove requested behavior, privacy protections, error handling,
  accessibility, or necessary checks to make code shorter.
- These are development rules; neither skill changes Sage's own voice or
  product behavior. Token savings are a goal, never a reason to sacrifice
  correctness or clarity, and never a guaranteed allowance increase.
- Apply this separation in every Sage task unless Elliot explicitly changes it.
  Conflicting skill instructions and older agent notes do not override it.

## Product frame

- Keep every accepted user turn and every valid assistant reply as episodic
  history. Ordinary, silly, and apparently insignificant moments belong too.
- Reconstruct relevance from the present conversation and situation at recall.
- Keep original events available. Episodes, associations, summaries, and
  tentative patterns retain their sources and remain revisable.
- Contradictory events remain history. Frozen facts or current-state tables
  must not replace original events.
- Sage may answer, notice, suggest, prepare, or act. Initiative must fit context,
  permission, and risk. Memory and capability serve daily life as a whole.

## Permanent boundaries

- Sage is single-user and local-first; models are replaceable engines.
- Lived memory belongs in `~/sage_data/`. Code, project records, and the identity
  seed (`directive.txt`) stay outside it. Earned identity entries live inside
  `~/sage_data/interior/`, as defined in `docs/INVARIANTS.md`.
- Keep relational memory and interior material physically separate.
- Provider calls use the configured local router and only necessary context.
- All accepted messages use the normal recall, embedding, provider, search, and
  background paths. No sensitive or local-only mode exists.
- Minimize provider context to what the active conversation or approved
  background capability needs. Never claim conversation content stays local-only.
- Preserve accepted local events when providers fail; report failure honestly.
- Store time in UTC and display it in WIB.
- Initiative stays in-app, with at most one revisable waiting message. External
  notifications and broader reach require an explicitly settled design.
- Sage must not claim human experience or sentience as fact.
- Risky, irreversible, external, expensive, or ambiguous actions require explicit
  authorization. This guide authorizes the routine commit/push step below.
- Do not write to lived memory, change `.env` or credentials, restart services,
  or perform destructive migrations without explicit user approval. Older
  permissions in `CLAUDE.md` do not override this boundary.
- Preserve unrelated local changes. Never commit secrets or include another
  unfinished change in a milestone commit.

## Engineering loop

1. State one behavior-sized outcome and its acceptance checks.
2. Inspect current changes, then trace the affected path and callers.
3. Make the smallest root-cause change that fulfills the outcome.
4. Run appropriate deterministic checks and report their actual results.
   Documentation-only changes need document and diff checks, not an unrelated
   application test run. Distinguish existing failures from new failures.
5. Review the diff separately for correctness, scope, privacy, and unwanted edits.
6. Commit and push each completed, verified milestone, including only its files.
7. Update affected project records when behavior or the product frame changes.

## Project authority

- `docs/NORTH_STAR.md` — purpose and felt outcome.
- `docs/INVARIANTS.md` — permanent constraints.
- `docs/DECISIONS.md` — settled decisions and superseded V3 notice.
- `docs/BLUEPRINT.md` — behavior map, not an implementation claim.
- `docs/MILESTONE.md` — current outcome and acceptance evidence.
- `README.md` — concise report of present reality.

Use this file for development workflow and the records above for product
authority. Follow Elliot's explicit instructions over skill defaults. Older V3
commits and agent notes are implementation history, not current product
authority. A generated project map is a navigation aid; verify it against
current files before relying on it.
