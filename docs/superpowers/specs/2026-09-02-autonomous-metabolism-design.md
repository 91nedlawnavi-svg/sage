# Autonomous Metabolism — Design

**Status:** Implemented 2026-09-03.

## Problem

Sage only thinks when spoken to. Between conversations she is inert — no
curiosity, no digestion, no initiative. The heartbeat extracts entities and
writes reflections, but that work is mechanical and never surfaces to Elliot.

The result: every conversation starts cold. Sage has memory but no metabolism.

## Design principle

Metabolism is a reaction to conversation, not an idle behavior. It fires because
something real came up, not because a timer says it should. Silence is the
default outcome; a waiting message is earned, not scheduled.

## Trigger

A metabolism cycle starts when Sage detects **conversation silence**: no new
user turn arrives within a configurable delay (`SAGE_METABOLISM_DELAY` env var,
default 300 seconds). This is checked by the heartbeat, which already runs on a
2-minute interval. If the heartbeat sees that the most recent user event is
older than the delay and no metabolism cycle has run for that event, it triggers
one.

A metabolism cycle runs **at most once per conversation silence**. The
completion record ties to the last user event id before silence fell, so
repeated heartbeats during the same silence are no-ops.

If no conversation happened since the last metabolism cycle, nothing runs.

## Pipeline

The cycle is a four-stage pipeline. Each stage gates the next: an empty result
at any stage stops the pipeline. No stage runs unless the previous one produced
something.

### Stage 1: Gap scan

Read the recent conversation (up to the last 10 non-sensitive user+assistant
turns). Ask the model:

> "You are Sage, reviewing a recent conversation with Elliot. Identify 1-3
> specific things from this conversation where you were uncertain, didn't know
> the answer, were curious, or noticed a gap in your understanding. Return a
> JSON list of objects: `[{"gap": "description", "query": "search query"}]`.
> If there are no genuine gaps or curiosity, return `[]`."

If the model returns an empty list or fails, the pipeline stops. No exploration,
no message.

Gaps are stored as a JSONL record in `~/sage_data/interior/metabolism.jsonl`
with `kind: "gap_scan"`, the source event id, the timestamp, and the gaps found.

### Stage 2: Explore

For each gap that has a search query (capped at 3), run `search()` from
`search.py` — the same SearXNG integration used in conversation. Store each
search result set as an episodic event via `EventStore.append()` with role
`"assistant"` and content formatted as `[Metabolism search: {query}]\nSources: ...`,
mirroring the conversational search format. These events are assistant-role,
so `provider_excluded` is not set by `append()`. They will appear in recall
and conversation context like any other assistant event. This is intentional:
if Sage searched something between conversations, that knowledge should be
available when she speaks next. The `[Metabolism search: ...]` prefix
distinguishes them from conversational replies.

If all searches return empty, the pipeline stops.

Store an exploration record in `metabolism.jsonl` with `kind: "exploration"`,
linking to the gap scan and listing what was found.

### Stage 3: Digest

One model call that synthesizes what was found. The prompt receives:
- The gaps that were explored
- The search results (formatted as context)
- The last few reflections (for continuity)

> "You are Sage, thinking privately after a conversation with Elliot. You
> noticed some gaps and searched for answers. Below are the gaps and what you
> found. Write a brief private note (2-4 sentences) connecting what you learned
> to the conversation. This is for your own notebook, not a message to Elliot.
> If the search results didn't actually resolve the gap or add anything
> interesting, say so honestly and keep it to one sentence."

The digest is stored as a reflection with `category: "metabolism"` and
`source_event_id` pointing to the last user event.

If the model fails or returns empty, the pipeline stops — no waiting message.

### Stage 4: Reach

Decide whether the digest warrants a waiting message. Not every metabolism
cycle should produce one — most shouldn't. The decision is a model call:

> "You are Sage. You just explored some gaps from your conversation with Elliot
> and wrote this private note:
>
> {digest}
>
> Should you leave Elliot a brief note about what you found? Only if you
> discovered something genuinely interesting or useful that he'd want to know.
> Do not leave a note just to show you were thinking. Most of the time the
> answer is no.
>
> If yes, write the note as you'd say it to him (1-3 sentences, warm and plain,
> starting with substance). If no, reply with exactly: NO_MESSAGE"

If the model says NO_MESSAGE or fails, no waiting message is set. If it
produces a note, call `interior.set_waiting_message(note)`.

The reach decision is stored in `metabolism.jsonl` with `kind: "reach"`,
recording whether a message was sent and its content.

## Storage

All metabolism records go in `~/sage_data/interior/metabolism.jsonl`, append-only,
same format as other interior JSONL files. Each record carries:

- `kind`: `"gap_scan"` | `"exploration"` | `"reach"`
- `id`: UUID
- `source_event_id`: the last user event that triggered this cycle
- `said_at`: UTC timestamp
- Kind-specific fields (gaps found, search results, message content)

The digest is stored as a regular reflection (with `category: "metabolism"`) so
it enters the existing reflection stream and is visible in the Notebook.

Exploration search results are stored as regular episodic events so they're
available for future recall.

## Completion tracking

A metabolism cycle's completion is tracked via
`EventStore.append_heartbeat_completion("metabolism", source_event_id)`. The
`heartbeat_completed` method needs to accept `"metabolism"` as a valid stage
(currently restricted to `"entities"` and `"reflection"`).

## What this design does NOT do

- **No chained curiosity.** A metabolism cycle does not trigger another one.
  The exploration stops after one round of searches per conversation.
- **No idle exploration.** If Sage has no conversation to react to, she does
  nothing. Timer fires, nothing triggers, silence.
- **No external actions.** Metabolism only reads the web and writes to local
  stores. No posting, no notifications beyond the waiting message.
- **No sensitive material.** Gap scan excludes sensitive events. Search queries
  are derived from non-sensitive conversation content only.
- **No guaranteed message.** Most metabolism cycles should produce no waiting
  message. The four-stage pipeline has four exit points where silence wins.

## Interaction with existing systems

- **Heartbeat:** Gains one new check at the end of `beat()` — trigger condition
  for metabolism. The metabolism pipeline itself runs in the heartbeat thread
  but as a separate function, not interleaved with extraction/reflection.
- **Waiting message:** Uses the existing `set_waiting_message`. Only one waiting
  message exists at a time (by design). A metabolism message can be overwritten
  by a newer one or cleared when Elliot speaks.
- **Search:** Reuses `search.py` unchanged.
- **Reflections:** Metabolism digests are reflections with `category: "metabolism"`.
  They appear in the Notebook alongside other reflections.
- **Events:** Exploration results are episodic events, recallable like any other.
- **Privacy:** Gap scan filters out sensitive events. The pipeline never reads
  sensitive content.

## Testing strategy

- Gap scan returns empty → pipeline stops (no exploration, no message)
- Gap scan finds gaps → exploration runs searches
- All searches empty → pipeline stops (no message)
- Successful exploration → digest reflection created
- Digest → reach decides NO_MESSAGE → no waiting message
- Digest → reach produces note → waiting message set
- Metabolism only runs once per conversation silence (completion tracking)
- Sensitive events excluded from gap scan input
- Metabolism JSONL records are well-formed and idempotent on replay

## Implementation order

1. ✅ Widen `heartbeat_completed` to accept `"metabolism"` stage. Add
   `metabolism.jsonl` path to `InteriorStore`. Add metabolism trigger check to
   heartbeat.
2. ✅ Gap scan function + storage.
3. ✅ Explore function + episodic event storage.
4. ✅ Digest function + metabolism reflection.
5. ✅ Reach function + waiting message.
6. ✅ Wire the pipeline into the heartbeat trigger.
7. ✅ Tests for each stage and the full pipeline.
8. ✅ Update MILESTONE and spec status.

## Settled questions

1. **Silence threshold:** configurable via `SAGE_METABOLISM_DELAY` env var,
   default 300 seconds. Loaded in `launch.py` alongside other env config.
2. **Metabolism reflections:** mixed into the Reflections tab with
   `category: "metabolism"`. No separate tab.
3. **Metabolism API:** `GET /api/metabolism` returns the last N metabolism
   records from `metabolism.jsonl` (gap scans, explorations, reach decisions)
   for review. Read-only, same trust/origin checks as other GET endpoints.
