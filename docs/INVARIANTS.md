# Sage — Invariants

These rules govern every implementation. Deferred means no surface exists yet; it does not weaken rule.

## Scope and routing

- Sage serves one local user only.
- User data stays local by default. Any provider use must be explicit in behavior and limited to necessary context.
- Model routing uses free-tier aliases only. Never introduce paid-model fallback.
- Provider failure degrades clearly without losing accepted local events.

## Identity and lived memory

- `~/sage_data/` is lived memory and must remain deletable as one unit.
- Identity, code, and project records stay outside `~/sage_data/`.
- A directive is authoritative once one exists. Directive work is deferred.

## Event memory

- A timestamped event is memory's core unit.
- Every event stores exact UTC `said_at`; `happened_at` may be fuzzy or absent.
- State is computed when recalling events, never stored as a replacement current view.
- Contradictions remain in history.
- Do not reintroduce frozen facts, locks, promotion queues or desks, reconciliation precedence, or current-state stores.

## Recall and privacy

- Recall must consider relevance at time of use, not only extraction time.
- Relational memory and Sage's interior memory remain physically separate.
- Held-close material is excluded tactfully at recall time and is never re-shipped by background provider work.
- Graph work, when introduced, indexes durable entities only. Entity/graph work is deferred.

## Time and reach

- Persist time in UTC. Display time in WIB.
- Reach is at most one revisable waiting message in app. Never send push notifications.
- Heartbeat and background reach behavior are deferred.

## Belief and agency

- Sage may change through argument and evidence.
- No direct belief-edit path exists beyond argument.
- Belief representation is deferred.
