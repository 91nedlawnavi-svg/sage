# Sessions, Context, Models, Voice, Retry, and Deletion

Working note — 2026-09-08

This note captures the current idea set. It is not a settled design or an
implementation-status claim. Work should proceed in small, independently
verified sessions. Decisions belong in `docs/DECISIONS.md` only after Elliot
settles them.

## Why these ideas arrived together

The split-voice latency trial exposed a chain of connected questions:

1. Voice latency depends heavily on the selected language model.
2. User-selectable models need a clear scope and fallback policy.
3. Model scope depends on what a Sage chat/session means.
4. Sessions depend on how Sage assembles provider context from local memory.
5. Normal session controls raise retry, archive, and permanent-deletion needs.

These are not isolated interface additions. They meet at Sage's context and
memory boundaries, so they should be designed together but built separately.

## Current reality

Sage has one append-only lifetime event history in `~/sage_data/`. It does not
send that entire history to the provider on every turn.

Today, `New chat` appends a `chat_boundary` record. The browser clears the
visible conversation, and events before the latest boundary stop qualifying as
immediate recent context. Those older events remain in lifetime history and can
still return through semantic recall.

For an ordinary message, Sage currently sends roughly:

1. Sage's directive and ratified identity.
2. Up to four recent visible events from the current chat.
3. Up to four older events selected by global recall.
4. Search context when needed.
5. The current user message.

The default context budget is therefore eight prior events, not a growing copy
of every conversation. The model's advertised 128K, 256K, or 1M context window
is only its maximum capacity. Sage currently uses a small fraction of even the
smallest candidate window.

Useful distinction:

```text
Lifetime memory
  All accepted local events, retained across chats
        |
Session or chat
  One conversational chapter in that lifetime
        |
Context assembly
  The small, relevant packet selected for this turn
        |
Model context window
  The provider model's hard maximum input capacity
```

## Preferred context behavior

Elliot wants Sage to reconstruct what matters instead of replaying an entire
old conversation. When an old session is reopened, the provider context should
combine three sources:

1. **Selected-session tail** — the last few turns from the old session, restoring
   its local thread and tone.
2. **Recent-life context** — a small amount of recent Sage conversation after
   that session, so Sage is not frozen at the old date.
3. **Global recall** — events from any session selected because they matter to
   the new message.

This is realistic. It needs four safeguards:

- Deduplicate events selected by more than one source.
- Keep final messages in chronological order inside clearly labelled context
  sections.
- Reserve a token budget for the current message and Sage's answer.
- Adapt the budget to the selected model without filling its entire advertised
  window merely because space exists.

For the newest active session, recent context and the selected-session tail will
usually be the same events. For a resumed old session, they are distinct: the
old tail restores that chapter, while recent-life context tells Sage what has
happened since.

The conservative first contract keeps the existing eight-event ceiling: up to
four selected-session tail events, up to two recent-life bridge events on the
first resumed turn, then global recall using the remaining room. Events are
deduplicated and returned to chronology. Real use may justify later token-based
tuning; larger advertised windows are not a reason to send more history by
default.

## Session model

Sage should gain real sessions while keeping one global memory.

Each new event should belong to a stable session ID. Each session should have:

- Stable ID.
- Created and last-active timestamps.
- User-editable title.
- Archive state.
- Selected chat model, defaulting to `Auto`.
- Ordered events, including text and associated split-voice turns.

Expected controls:

- New chat.
- List chats.
- Open and continue an old chat.
- Rename.
- Archive and unarchive.
- Search chats.
- Delete permanently through a separate destructive flow.

Opening an old session should resume it, not merely show a read-only transcript.
New events can remain append-only in physical time while carrying the old
session's ID.

Legacy events must not be rewritten merely to add session IDs. Existing
`chat_boundary` records can define legacy sessions at read time. New session
metadata can be additive and append-only.

Possible later controls, not first-milestone requirements:

- Duplicate or branch a chat.
- Export one chat.
- Pin chats.
- Move a voice call between sessions after review.

## Model picker

Normal text chat and split voice should offer an accessible model picker.

### `Auto`

`Auto` means Sage uses the configured ordered fallback chain. The interface
should report which model actually answered.

### Explicit model

Selecting a specific configured model means only that model should answer. If
it fails, Sage must not silently switch engines. The interface should show a
gentle red `Model error` row with a `Retry with Auto` action.

Model selection should be stored per session. New sessions should begin on
`Auto`. Background extraction, heartbeat, and metabolism should remain on their
configured automatic routes rather than inheriting a foreground chat choice.

The picker should list configured conversational models, not every image,
embedding, transcription, or experimental endpoint returned by the local
router. How that configured list is maintained remains open; it should be easy
to update when a provider adds models.

## Retry behavior

Retry should be a baseline interface behavior for safely repeatable failures,
not a model-picker special case.

Important rules:

- The original user event is saved once before a provider call.
- Retrying a failed answer reuses that event; it must not create duplicate user
  memory.
- A failed or incomplete assistant stream is not stored as a valid assistant
  reply.
- Explicit-model failure offers `Retry` and `Retry with Auto`.
- Other safe failures offer the action appropriate to them: resend, reload,
  retry transcription, or retry speech generation.
- Search failure need not destroy an otherwise valid answer; search status and
  answer status should remain distinguishable.
- Retries must not blindly repeat an external or irreversible action.

The error row should be visible without becoming alarming: muted red surface,
plain-language cause, attempted model when relevant, and one clear primary
retry action.

## Voice model behavior

Direct Gemini Live remains available but is no longer the main development
path. It keeps its fixed Gemini Live model and existing direct-audio behavior.

Split voice becomes the main experimental voice path. It uses Deepgram STT,
Sage's normal text reasoning, and Deepgram TTS. It should share the active
session's memory and persist its transcript in that session.

Open decision: model inheritance when entering split voice.

Possible behavior:

- `Same as chat` preserves one engine across text and voice.
- A separate voice override allows a faster model without changing text chat.
- A voice-specific `Auto` chain could prioritize latency, while chat `Auto`
  prioritizes quality and reliability.

The picker should make the active choice obvious on the call screen. Context
window size is not the forcing constraint today: Mistral Small 4, Ministral 8B,
and Ministral 14B each advertise 256K context, while current Sage context is far
smaller. The real tradeoff is continuity versus response latency.

## Current model evidence

One fixed eight-situation Sage screen measured first-complete-sentence latency,
completion reliability, and 24 written conversational checks. Scores represent
useful intelligence for Sage — continuity, judgment, restraint, contradiction
handling, correction acceptance, grounded curiosity, and specific comfort —
not general benchmark intelligence.

| Model | Median first sentence | Reliability | Sage checks |
| --- | ---: | ---: | ---: |
| Ministral 14B | 0.61s | 8/8 | 21/24 |
| Ministral 8B | 1.26s | 8/8 | 19/24 |
| Qwen3.8 Max, alternate working route | 2.73s | 8/8 | 20/24 |
| Mistral Small 4 | 2.74s | 8/8 | 17/24 |
| DeepSeek V4 Flash | 3.92s | 8/8 | 23/24 |
| SenseNova 6.8 Flash-Lite | 5.64s successful median | 6/8 | 15/24 first pass |
| MiniMax M2.7 Highspeed | 6.67s | 8/8 | 23/24 |
| Qwen3.6 35B-A3B | no valid replies | 0/8 | not scored |

Important caveats:

- Provider route and model are separate variables. Elliot measured 40.138s of
  Sage thinking through the current Qwen3.8 Max route, while another route to
  the same named model was much faster.
- SenseNova produced promising replies when available, but one simple restraint
  prompt timed out twice. Another timeout recovered on retry.
- Eight prompts and manual scoring are a screening result, not a permanent model
  verdict.
- No production model setting changed during the comparison.

## Archive and permanent deletion

Archive hides a session and remains reversible. It must not remove events from
global recall unless a later privacy control explicitly defines that behavior.

Delete is different. Elliot wants actual memory removal, guarded by an explicit
confirmation flow:

1. User chooses `Delete permanently`.
2. Sage explains that deletion cannot be undone.
3. Sage shows the session title and counts of affected records.
4. User must type `DELETE` exactly.
5. Only then may Sage perform the purge.

A complete active-store purge may need to remove:

- User and assistant events in the session.
- Voice transcript corrections and call grouping records.
- Embeddings for deleted events.
- Entity observations sourced only from deleted events.
- Search records and heartbeat completion records tied to deleted events.
- Relational SQLite mirror rows.
- Session title, archive state, and model preference.
- Any waiting or derived records tied solely to the session.

Hard unresolved issue: reflections, identity proposals, ratified identity, or
later conclusions may have been influenced by deleted events. Sage cannot claim
true downstream erasure unless those records carry enough source provenance to
identify the influence safely. Deleting shared derived knowledge may also erase
information supported by other sessions.

External backups are outside Sage's active stores and cannot be silently erased
by an in-app delete. The confirmation text must state that boundary honestly.

Deletion creates a deliberate exception to append-only preservation. It should
use an atomic rewrite of affected authoritative JSONL files and synchronized
mirror cleanup only after confirmation. It must never leave a hidden recovery
copy that contradicts the promise of permanent deletion.

## Small implementation sessions

### Session 1 — Settle context contract — complete

- Define selected-session tail, recent-life bridge, and global-recall budgets.
- Define deduplication and ordering.
- Test new, long-running, and resumed-old-session examples.
- Record settled behavior before changing persistence.

### Session 2 — Add session identity safely — complete

- Add stable session metadata for new events.
- Interpret legacy boundaries as legacy sessions without rewriting events.
- Keep `~/sage_data/` JSONL authoritative.
- Verify restart and mirror behavior.

### Session 3 — Add session navigation — complete

- List, open, continue, rename, archive, and unarchive.
- Keep global recall available across sessions.
- Verify keyboard, mobile, and screen-reader behavior.

### Session 4 — Add model selection and retries — complete

- Per-session `Auto` or explicit configured model.
- Honest actual-model display.
- Generic retry foundation without duplicate user events.
- `Model error` and `Retry with Auto`.

### Session 5 — Connect split voice

- Associate split-voice turns with active session.
- Settle and implement chat-model inheritance versus voice override.
- Preserve call review and transcript correction.
- Re-measure end-to-first-audio latency by model.

### Session 6 — Design and implement purge

- Map every directly and indirectly derived record.
- Add missing provenance before promising complete deletion.
- Build typed `DELETE` confirmation and exact scope preview.
- Test interruption, atomicity, restart, mirrors, recall, and external-backup
  disclosure.

### Session 7 — Final integration review

- Review privacy, memory integrity, failure behavior, and unwanted scope.
- Run full deterministic tests and live browser checks.
- Update canonical blueprint, decisions, milestone, and timeline only for
  behavior that actually shipped.

## Questions still open

- Does real use justify replacing the conservative eight-event ceiling with a
  token-based budget?
- Should the small raw recent-life bridge eventually become a local summary or
  relevance-selected bridge?
- Should titles use the first user message, a local rule, or a model-generated
  summary?
- Should archive affect global recall, or only sidebar visibility?
- Should split voice default to `Same as chat`, a separate fast `Auto`, or the
  last voice choice?
- Where should the configured picker list live so new models are easy to add?
- Should a failed partial assistant response remain visible but unsaved, or be
  replaced by the error row?
- May a minimal non-content tombstone record that a deletion occurred, or must
  even the session's existence disappear?
- What provenance is required before deleting reflections and identity material
  influenced by a removed session?

## Working discipline

This feature set warrants High reasoning before implementation because it
changes context assembly, destructive memory behavior, and cross-surface model
routing. Each session should settle one behavior-sized outcome, verify it,
review its diff separately, and commit only that milestone. No `.env` changes,
lived-memory rewrites, or destructive migration should happen implicitly.
