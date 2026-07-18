# Wave 2 — SQLite Memory Core: Schema Design (DRAFT for Elliot's approval)

Status: DRAFT 2026-07-18. Implements Blueprint §2. Plain-language summary at top;
exact DDL below for the record. Nothing here is code yet.

## What this is, in behavior terms

One file, `~/sage_data/sage.db`, becomes Sage's whole structured memory: what
happened (episodes), what she's noticing (impressions), what she knows (facts),
who's who (entities), how things connect (relations), what she doesn't know
(gaps), what she believes (interior store, separate tables), and a tamper-proof
diary of every change ever made (audit log). `rm -rf ~/sage_data` still wipes
everything — Day-0 stands.

Two physical stores, one engine: `relational.db` (Elliot's world) and
`interior.db` (her mind) as **separate SQLite files**, so the contamination
wall is enforced by file handles, not query discipline. A bug in one query
cannot read across a file it never opened.

## Design rules (from blueprint, non-negotiable)

- UTC tz-aware ISO8601 everywhere in storage; WIB is display-only.
- WAL mode, busy_timeout=5000ms, single-writer asyncio queue in-process;
  cross-process tools open read-only and degrade on lock.
- Stable ids: `entity_id` = random UUID hex, never derived from display name.
- Nothing is ever DELETEd: tombstones + supersede chains + audit log.
- Every mutation writes an audit row in the same transaction.
- Held-close hooks land now (columns + exclusion indexes), behavior in Wave 3.

## relational.db — tables

### episodes
| column | type | notes |
|---|---|---|
| id | TEXT PK | uuid hex |
| ts | TEXT | UTC ISO8601, when it happened |
| source | TEXT | conversation \| search \| reflection-adjacent |
| speaker | TEXT | elliot \| sage \| web |
| content | TEXT | near-verbatim |
| tone_hint | TEXT NULL | e.g. "venting" — extraction's read, loosely held |
| held_close | INTEGER | 0/1 — held-close span member (Wave 3 behavior) |
| held_close_origin | TEXT NULL | she-sensed \| elliot-tap |
| source_key | TEXT UNIQUE | migration/provenance key (old JSONL id) |

Append-only forever. No updates except the held_close pair (toggle is
retroactive-downstream, an UPDATE + audit row).

### impressions
| column | type | notes |
|---|---|---|
| id | TEXT PK | |
| ts_formed | TEXT | |
| statement | TEXT | "Elliot seems unhappy at work" |
| support_count | INTEGER | episodes feeding it |
| status | TEXT | active \| faded \| contradicted \| promoted |
| valid_from / superseded_by | | same history semantics as facts |

### impression_support (join)
impression_id → episode_id, n:m, provenance for every impression.

### facts
| column | type | notes |
|---|---|---|
| id | TEXT PK | |
| subject_entity | TEXT FK entities | |
| predicate | TEXT | canonicalized |
| object_kind | TEXT | entity \| literal \| date |
| object_value | TEXT | entity id or literal |
| epistemic | TEXT | asserted \| believed-by:<entity> \| derived |
| origin | TEXT | she \| elliot \| elliot-locked |
| locked | INTEGER | sticky-note: locked wins, survives re-derivation |
| tombstoned | INTEGER | soft delete, drawer can resurrect |
| valid_from | TEXT | |
| superseded_by | TEXT NULL FK facts.id | supersede-with-history chain |
| confidence | REAL | |
| promoted_from | TEXT NULL | impression id (promotion desk provenance) |
| approved_by_elliot | INTEGER | promotion gate — facts require explicit OK |

### entities
| column | type | notes |
|---|---|---|
| id | TEXT PK | uuid — NOT name-derived (fixes the Maya bug) |
| type | TEXT | person \| place \| project \| event \| org \| thing |
| display_name | TEXT | |
| tombstoned | INTEGER | |
| merged_into | TEXT NULL | merge provenance; split = new rows + audit |

### entity_aliases
entity_id, alias, added_ts — aliases are rows, not a JSON blob (queryable dedup).

### relations
Same shape as facts minus promotion columns (subject_entity, predicate,
object_kind/value, epistemic, origin, locked, tombstoned, valid_from,
superseded_by, confidence). Kept as its own table because relations link
entities; facts may hold literals. (If survey shows the old store treats these
as one thing, they may collapse into one table — decision pending survey.)

### gaps
| column | type | notes |
|---|---|---|
| id | TEXT PK | |
| about_entity | TEXT FK | |
| description | TEXT | "when Paul joined the class" |
| spawned_from | TEXT | episode/fact id |
| status | TEXT | open \| answered \| stale |
| answered_by | TEXT NULL | fact id that closed it |

### provenance (join)
fact_or_relation_id → episode_id. Every derived line traces to episodes.

### audit_log
| column | type | notes |
|---|---|---|
| seq | INTEGER PK AUTOINCREMENT | |
| ts | TEXT | |
| actor | TEXT | she \| elliot \| migration \| consolidation |
| action | TEXT | insert \| supersede \| lock \| tombstone \| merge \| split \| promote \| approve \| toggle-held-close |
| table_name, row_id | TEXT | |
| detail | TEXT | JSON blob of the change |

Append-only, same transaction as the mutation it records.

### waiting_message (reach, Wave 3 behavior — schema lands now)
Single-row table: content, thread_ref, written_ts, revised_ts, surfaced, read_ts.
One pending max is a table constraint (CHECK on singleton row id=1).

## interior.db — tables

episodes-equivalent (her reflections/readings) + **stance_events** (what she
read, source, direction, why, ts) + **beliefs** (topic, direction, weight,
hardened flag, steelman_done flag) + belief_history + source_trust
(domain, substance/slop/burned counters) + its own audit_log. Break-glass
override rows carry `origin='elliot-override'` — visible, dated, never silent.
No table in interior.db references entities/facts in relational.db.

## What dies with this schema

- Name-derived ids (`person:maya` forever-merge bug)
- Full-file rewrite per append; silent write-failure-as-success
- Unstable ids across reconcile (uncorrectable facts)
- No history on corrections; no audit trail
- Naive-local timestamps

## Migration mapping (pending survey confirmation)

conversation.jsonl → episodes (source=conversation, source_key=old id)
reflections/findings.jsonl → interior episodes
relational entities/relations JSONL → entities/aliases/relations + facts,
  locked lines → locked=1, all with actor=migration audit rows
Old cursor files → retired (SQLite cursor = processed flag per episode)
