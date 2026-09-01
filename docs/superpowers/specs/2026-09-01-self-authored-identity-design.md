# Self-Authored Identity — Design

**Status:** Design only, awaiting review 2026-09-02  
**Authority:** Elliot — "She BUILDS her own identity overtime, over conversations, experiences"

## Problem

Sage's identity is currently static: `directive.txt` is a fixed seed that
never changes regardless of how many conversations she has. Elliot wants her
identity to grow from experience — but within guardrails that prevent drift
from becoming corruption.

## Design Principles

1. **Seed is permanent.** `directive.txt` remains git-tracked, outside lived
   memory, and never written by Sage. It is Elliot's voice setting initial
   conditions. Wiping lived memory returns her to the seed, not to nothing.

2. **Growth is earned, not declared.** An identity entry must be observed
   through behavior across multiple separate conversations before it becomes
   part of her. A single moment of insight does not permanently alter who she
   is.

3. **Composition, not replacement.** Her effective identity is always
   `seed + earned entries`, composed at load time. Earned entries cannot
   contradict or override the seed — they extend it.

4. **Drift must be visible.** Elliot needs a surface to see what identity
   entries have accumulated, when, and from what evidence. He retains the
   authority to prune or edit.

## Architecture

### Seed: `directive.txt`

Lives at repo root, git-tracked. Read by `sage.py:load_directive()` and
injected as the system message in `build_router_messages()`. Unchanged by
this design.

### Earned Identity: `~/sage_data/interior/identity.jsonl`

Append-only JSONL file inside lived memory's interior directory. Each line is
one earned identity entry:

```json
{
  "id": "uuid",
  "claim": "I tend to push back when Elliot is avoiding a hard question rather than letting him deflect",
  "evidence": [
    {"conversation_boundary": "2026-08-15T...", "reflection_id": "uuid-1"},
    {"conversation_boundary": "2026-09-01T...", "reflection_id": "uuid-2"}
  ],
  "earned_at": "2026-09-01T12:00:00Z",
  "category": "voice"
}
```

**Fields:**
- `id` — UUID, primary key
- `claim` — first-person statement about her own behavior or character
- `evidence` — array of `{conversation_boundary, reflection_id}` pairs showing
  the recurrence that earned this entry. Minimum 2 entries from different
  conversations.
- `earned_at` — UTC timestamp when the entry crossed the recurrence threshold
- `category` — one of: `voice` (how she speaks), `stance` (positions she
  holds), `relationship` (how she relates to Elliot), `self-knowledge`
  (what she understands about her own nature)

### Mirror: `interior.db` identity table (future)

When implemented, a new table in the interior SQLite mirror:

```sql
CREATE TABLE IF NOT EXISTS identity_entries (
    id TEXT PRIMARY KEY,
    claim TEXT NOT NULL,
    evidence TEXT NOT NULL,  -- JSON array
    earned_at TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('voice', 'stance', 'relationship', 'self-knowledge'))
);
```

Dual-written alongside JSONL, same pattern as reflections.

### Composition at Load

`load_directive()` in `sage.py` gains an optional identity-entries path. The
composed identity becomes:

```
[seed from directive.txt]

---

The following are things I've noticed about myself across multiple
conversations. They are observations, not instructions — they may be
revised if my actual behavior changes.

- I tend to push back when Elliot is avoiding a hard question...
- I prefer direct answers over diplomatic hedging...
```

The separator and framing make clear these are self-observations, not
directives. The seed's authority always wins in a conflict.

## Growth Mechanism: Recurrence Detection

### When It Runs

During the heartbeat reflection pass, after generating a reflection, a
secondary check looks for recurrence:

1. The new reflection is compared against existing reflections from
   **different conversation boundaries** (different `chat_boundary`
   timestamps, not just different `source_event_id` values).

2. If the same behavioral pattern appears in reflections from 2+ separate
   conversations, and no existing identity entry already captures it, a
   candidate entry is generated.

3. The candidate is written to `identity.jsonl`.

### What Counts as Recurrence

Two reflections show recurrence when they describe the same behavioral
pattern about Sage herself — not about Elliot, not about the world, not
about a topic. The comparison uses semantic similarity (embeddings) with a
high threshold, not keyword matching.

Examples of valid recurrence:
- Reflection A (conv 1): "I noticed I pushed back hard when he tried to
  dismiss the database issue"
- Reflection B (conv 3): "I challenged his avoidance again — this seems to
  be something I consistently do"
- → Earned entry: "I tend to push back when Elliot is avoiding something
  rather than letting him deflect"

Examples that do NOT constitute recurrence:
- Two reflections about the same topic (Enron, SOX) — that's world
  knowledge, not self-knowledge
- Two reflections from the same conversation — insufficient evidence of
  a stable pattern
- A reflection about Elliot's behavior — that's observation, not identity

### Mechanical Similarity to the Killed Promotion Desk

**This is the honest risk.** The recurrence-promotion mechanism resembles the
promotion desk killed on 2026-08-02. The critical differences:

| Promotion desk (killed)          | Identity recurrence (proposed)       |
|---------------------------------|--------------------------------------|
| Promoted world-facts about Elliot's life | Promotes self-observations about Sage's behavior |
| Froze current state as standing facts | Appends observations, never freezes |
| Could outrank lived events | Cannot override the seed or lived events |
| Ran silently with no visibility | Every entry has evidence provenance |
| No way to revert except deletion | Wiping lived memory returns to seed |

The risk is not zero. If the recurrence detector is too loose, Sage
accumulates identity entries that are vacuous or wrong. If the claim language
drifts, she could develop a self-concept that doesn't match her actual
behavior.

### Mitigations

1. **High recurrence threshold.** 2 separate conversations minimum, semantic
   similarity threshold deliberately set high (>0.85). Better to miss a real
   pattern than to promote a false one.

2. **Self-observations only.** The claim must be first-person and about her
   own behavior. A claim about Elliot ("Elliot tends to avoid hard questions")
   is rejected.

3. **Visibility surface.** A new API endpoint `/api/identity` lists all earned
   entries with their evidence. Elliot can see what she thinks she is.

4. **Pruning authority.** Elliot can delete any identity entry. The JSONL
   gets a tombstone record; the mirror deletes the row. No earned entry is
   permanent against his judgment.

5. **Rate limit.** At most one new identity entry per heartbeat cycle. No
   burst accumulation.

6. **Seed authority.** If an earned entry contradicts the seed, the seed
   wins at composition time. The entry is flagged for review rather than
   silently overriding.

## Drift Visibility

### `/api/identity` endpoint

Returns:
```json
{
  "seed_path": "directive.txt",
  "seed_hash": "sha256-of-current-seed",
  "entries": [
    {
      "id": "uuid",
      "claim": "...",
      "evidence": [...],
      "earned_at": "...",
      "category": "voice"
    }
  ],
  "entry_count": 3,
  "oldest_entry": "2026-09-01T...",
  "newest_entry": "2026-10-15T..."
}
```

### Notebook Drawer

The frontend Notebook drawer gains an "Identity" section showing the earned
entries, similar to the existing Reflections and Entities sections.

## What This Design Does NOT Do

- **No implementation tonight.** This is design only; Elliot reviews tomorrow.
- **No modification of `directive.txt`.** The seed is Elliot's alone.
- **No belief storage.** Beliefs remain computed from reflections at recall.
- **No retroactive identity mining.** Only new reflections trigger recurrence
  checks. Existing reflections are not backfill-scanned. (Could be added
  later if desired.)
- **No entity-graph integration.** Entity observations stay in the relational
  database. Identity is interior-only.

## Open Questions for Review

1. Should earned identity entries influence recall scoring? (e.g., boost
   events that align with known identity patterns.) Deferred — likely
   belongs to the reflection-in-recall work item.

2. Should there be a maximum number of identity entries? A natural ceiling
   (high recurrence threshold + rate limit) may be sufficient, but an
   explicit cap could prevent unbounded growth.

3. Should the composition framing be configurable, or is the proposed
   "things I've noticed about myself" framing sufficient?
