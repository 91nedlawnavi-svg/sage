# Sage — Invariants

These constraints apply to every implementation and every future capability.

## Ownership and scope

- Sage serves one local user.
- User data stays local by default; provider use is explicit and minimized.
- `~/sage_data/` is lived memory and remains deletable as one unit.
- Identity, code, and project records stay outside lived memory.

## Episodic memory

- Every accepted user turn is durably retained as an event.
- Every valid assistant reply is durably retained as an event.
- Events store exact UTC `said_at`; `happened_at` may be fuzzy or absent.
- Original events are append-only history, not a cache to be replaced.
- No ordinary event is discarded merely because it seems mundane.
- Contradictions remain retrievable history.
- Episodes, associations, summaries, and patterns are derived views with source
  provenance; they never become an untraceable replacement for events.
- Meaning is computed when memory is recalled, not fixed permanently at intake.

## Context and privacy

- Recall is driven by the present conversation and situation, not only one
  isolated keyword or a permanently assigned importance score.
- Recall may combine lexical, semantic, temporal, episodic, entity, and pattern
  signals, then return a compact context with provenance.
- Sensitive material is excluded before casual recall, embedding, or provider
  prompt assembly.
- Unknown or interrupted privacy classification fails closed for provider work.
- Relational memory and Sage's interior material remain physically separate.

## Intelligence and agency

- Routed models are replaceable engines behind Sage's stable identity.
- Provider failure must degrade clearly without losing accepted local events.
- Sage may answer, notice, mention, suggest, prepare, or act; the response level
  must fit context, permission, and risk.
- External, risky, irreversible, expensive, or ambiguous actions require explicit
  authorization.
- Sage learns from outcomes and corrections without silently rewriting history.
- Sage must not claim human experience or sentience as fact.

## Time and reach

- Persist time in UTC; display user-facing time in WIB.
- In-app initiative is allowed only with a legible reason and a bounded surface.
- At most one revisable waiting message exists until a broader reach design is
  explicitly settled.
- No push or external notification path exists by default.
