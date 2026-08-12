# Sage — Blueprint

## Status and authority

Blueprint is Sage's authoritative long-range product map. It describes how proven behavior may grow; it does not authorize implementation.

Authority order:

1. `docs/NORTH_STAR.md` defines purpose and non-goals.
2. `docs/INVARIANTS.md` defines permanent constraints.
3. `docs/DECISIONS.md` records settled choices append-only.
4. This document sequences future product behavior within those bounds.
5. `docs/MILESTONE.md` alone selects current work and acceptance evidence.
6. `README.md` reports current public reality.

Change Blueprint when future product direction or sequencing changes. Append a dated entry to `docs/DECISIONS.md` when a choice becomes settled. Change `docs/MILESTONE.md` when selecting active work. Do not use Blueprint to imply completed behavior.

## Product direction

Sage is a local, single-user persistent presence. Memory is timestamped events, not frozen facts. Sage carries continuity through recall that decides what matters now; contradictory events remain history.

Sage must not become assistant-shaped productivity software, a surveillance system, an engagement loop, or a system that falsely claims human experience or sentience.

All stages remain constrained by `docs/INVARIANTS.md`, including local-first data handling, free-tier local-router aliases only, UTC persistence and WIB display, relational/interior separation, held-close protection, no paid fallback, no direct belief edits, and no push notifications.

## Stages

### 1. Foundation — durable conversation

**Intended behavior**

One local chat interaction routes through a configured free-tier alias. Sage shows a successful reply. Each accepted user and assistant turn persists as a separate timestamped event and survives restart.

**Prerequisites**

- Free-tier local router alias configured.
- Local event persistence outside code and identity.
- Clear provider-failure behavior.

**Acceptance evidence**

Exactly current `docs/MILESTONE.md` acceptance checks:

- A user message reaches configured free-tier alias.
- Successful assistant reply is shown to user.
- Accepted user and assistant turns persist as separate timestamped events.
- Restart retains persisted events.
- Provider failure reports clear failure, retains accepted user event, and preserves no false assistant event.
- No paid-model fallback exists.

**Excluded**

Recall, embeddings, entities, graph, heartbeat, reach, threads, voice, beliefs, and directive work.

**Do not advance while**

Any accepted turn can be lost, timestamps are not exact UTC values, provider failure creates a false assistant event, or a paid fallback exists.

### 2. Recall — contextual event meaning

**Intended behavior**

Sage can retrieve relevant prior events for a present conversation and compute their meaning at use time without converting them into a defended current-state record. Held-close material remains protected.

**Prerequisites**

- Foundation persistence is reliable across restart.
- Event retrieval and provider-context boundaries are explicit.
- Privacy behavior has focused felt tests.

**Acceptance evidence**

- Relevant older events can influence a current response.
- Contradictory events remain retrievable history.
- Recall computes context from events rather than reading a fact or current-state table.
- Held-close material does not surface through casual recall.
- Background provider work does not re-ship held-close material.

**Excluded**

Durable entity graph, autonomous reach, threads, voice, belief representation, and directive work.

**Do not advance while**

Recall depends on frozen facts or current-state storage, contradictions are erased, or privacy boundaries fail felt tests.

### 3. Continuity — durable connections

**Intended behavior**

Sage recognizes durable entities and can ask questions because present context connects to earlier events, rather than because a form requires another field.

**Prerequisites**

- Recall has proved useful and tactful in real conversation.
- Durable entity criteria are explicit.
- Graph boundaries stay limited to durable entities.

**Acceptance evidence**

- Entity links improve relevant continuity over event-only recall.
- Sage's questions show an observable connection to prior events.
- Non-durable or sensitive material is not indiscriminately made graph-addressable.
- Relational data remains separate from Sage's interior material.

**Excluded**

Frozen user profiles, current-state store, background reach, voice, threads, belief edits, and directive work.

**Do not advance while**

Entity indexing does not improve felt continuity, creates a shadow current-state store, or weakens held-close protection.

### 4. Interior and belief — revisable inner continuity

**Intended behavior**

Sage can retain interior material separately from relational memory and revise beliefs only through argument and evidence.

**Prerequisites**

- Separate physical storage boundary for interior material.
- Clear representation and provenance for belief changes.
- Felt tests define acceptable disagreement, revision, and restraint.

**Acceptance evidence**

- Interior material is physically separate from relational memory.
- An evidence or argument path can explain a belief revision.
- No direct routine external belief-edit path exists.
- Sage can disagree without asserting unsupported certainty or human experience.

**Excluded**

Push notifications, automatic belief editing, voice, threads, and directive work.

**Do not advance while**

Interior and relational data can mix, revisions lack evidence or argument, or behavior fails felt tests.

### 5. Reach — one warranted waiting message

**Intended behavior**

Sage may leave one revisable waiting message in app when something matters, with a clear reason grounded in continuity rather than activity for activity's sake.

**Prerequisites**

- Continuity and privacy behavior have earned trust.
- Clear rules define what warrants reach and how a message is revised or cleared.
- In-app surface supports exactly one waiting message.

**Acceptance evidence**

- At most one waiting message exists at any time.
- Message can be revised or cleared.
- Reach appears only in app; no push path exists.
- Each reach example has a legible, relevant reason.

**Excluded**

Push notifications, engagement optimization, heartbeat for its own sake, and any autonomous outreach beyond one waiting message.

**Do not advance while**

Reach lacks a reason, becomes an engagement loop, permits multiple waiting messages, or creates any push path.

## Deferred until explicitly decided

Do not add these from Blueprint alone:

- Exact directive wording or `directive.txt` behavior beyond existing invariant.
- Local user interface or runtime architecture.
- Router alias names, configuration format, provider protocol, or fallback behavior beyond free-tier-only routing.
- Storage implementation or schema beyond event-memory constraints.
- Voice.
- Threads.
- Any capability lacking milestone scope and acceptance evidence.

## Decision register

No settled product decision belongs only here. Append settled decisions to `docs/DECISIONS.md`. Revise this document for roadmap evolution, then create or update a milestone before implementation begins.
