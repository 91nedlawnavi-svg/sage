# Sage — Invariants

These constraints apply to every implementation and every future capability.

## Ownership and scope

- Sage serves one local user.
- Lived memory is stored locally; configured model providers receive only the
  current message and compact context needed for active Sage work.
- `~/sage_data/` is lived memory and remains deletable as one unit.
- Identity has two layers: the seed (`directive.txt`, git-tracked, outside
  lived memory) sets initial conditions; earned identity entries live inside
  lived memory (`~/sage_data/interior/`). Wiping lived memory returns Sage to
  the seed, not to nothing. Code and project records stay outside lived memory.

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

## Context and provider use

- Recall is driven by the present conversation and situation, not only one
  isolated keyword or a permanently assigned importance score.
- Recall may combine lexical, semantic, temporal, episodic, entity, and pattern
  signals, then return a compact context with provenance.
- Ordinary messages may be recalled, embedded, searched, or included in
  configured provider prompts when relevant.
- Sage has no per-message sensitive or local-only mode and must not claim that
  conversation content remains local-only.
- Provider context is minimized to what the active conversation or approved
  background capability needs.
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
