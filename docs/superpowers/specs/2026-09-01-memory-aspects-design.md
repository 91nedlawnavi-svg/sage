# Memory Aspects Taxonomy — Design

**Status:** Approved 2026-09-01  
**Authority:** Elliot — "Taxonomy 100% correct"

## Problem

Sage needs a clean taxonomy for what she stores about herself and the world.
The previous codebase had a `Belief` TypedDict and `beliefs.jsonl` that were
never populated, and the relationship between reflections, beliefs, entities,
and embeddings was undefined.

## Approved Taxonomy

### 1. Reflections (interior)

Private, append-only observations Sage makes about conversation and relationship.
Written by the heartbeat reflection pass after each exchange.

- **Storage:** `~/sage_data/interior/reflections.jsonl` (JSONL source) + `interior.db` reflections table (mirror)
- **Fields:** id, content, said_at, category, source_event_id
- **Write path:** `InteriorStore.append_reflection()` called by `heartbeat._reflection_pass()`
- **Read path:** `InteriorStore.list_reflections()` → Notebook drawer `/api/reflections`
- **Not yet wired into:** `build_router_messages()` — reflections do not influence replies yet

### 2. Identity (interior) — *design only, not yet implemented*

Sage's self-authored sense of who she is, layered on top of the seed
(`directive.txt`). See `2026-09-01-self-authored-identity-design.md`.

- **Seed:** `directive.txt` (git-tracked, Elliot's, outside lived memory)
- **Earned entries:** `~/sage_data/interior/identity.jsonl` (future)
- **Composition:** seed + earned entries at load time
- **Growth trigger:** recurrence — same self-observation noticed twice across separate conversations

### 3. Entities (relational)

Durable things in the world: people, projects, recurring topics. Currently
flat observations; future upgrade to a proper entity graph.

- **Storage:** `~/sage_data/relational/entities.jsonl` (JSONL source) + `relational.db` entity_observations table (mirror)
- **Fields:** entity_id, name, observation, said_at, source_event_id
- **Write path:** `EventStore.append_entity_observation()` called by `heartbeat._extract_entities_pass()`
- **Read path:** `EventStore.entity_observations()` → Notebook drawer `/api/entities`
- **Known quality issue:** 13/25 observations are vacuous ("mentioned in message"); extraction prompt needs work
- **Future:** entity graph with typed relationships, deduplication, temporal tracking

### 4. Embeddings (relational)

1024-dimensional vector representations of non-sensitive events, used for
semantic similarity in recall.

- **Storage:** `~/sage_data/relational/embeddings.jsonl` (JSONL source) + `relational.db` embeddings table (mirror)
- **Fields:** event_id, vector (JSON array of floats)
- **Write path:** `EventStore._save_embedding()` during `append()` for non-excluded events
- **Read path:** `EventStore._load_embeddings()` — prefers SQLite mirror, falls back to JSONL parse
- **Improvement shipped:** SQLite indexed read replaces full 2.2MB JSONL parse per recall

### 5. Beliefs — *computed, never stored*

**Decision:** Beliefs are computed at recall from the reflection stream. No
`beliefs` table, no `beliefs.jsonl`, no write path. This prevents the
standing-state drift that killed the previous facts/promotion design
(2026-08-02).

A belief is a pattern that emerges when multiple reflections converge on the
same theme. The mechanism for surfacing these patterns at recall time is not
yet built — it belongs to the reflection-in-recall work item.

## Physical Separation

| Aspect     | Database       | Directory      | Rationale |
|-----------|---------------|---------------|-----------|
| Reflections | interior.db   | `interior/`    | Sage's private inner life |
| Identity   | interior.db   | `interior/`    | Sage's self-concept |
| Entities   | relational.db | `relational/`  | Shared record of the world |
| Embeddings | relational.db | `relational/`  | Derived from shared events |

This mirrors the INVARIANTS.md requirement: "Relational memory and Sage's
interior material remain physically separate."

## What This Does Not Cover

- **Events** are not an "aspect" — they are the foundational memory unit from
  which all aspects are derived.
- **Privacy records**, **chat boundaries**, and **heartbeat completions** are
  operational metadata, not memory aspects.
- **Waiting message** is a transient communication surface, not a memory aspect.
