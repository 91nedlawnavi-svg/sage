# Sage — Current Milestone

## Sage Refresh

The old V3 rebuild is sealed as historical implementation work. It is not the
current product definition. The current work is to make the existing foundation
serve the refreshed picture: an owned personal intelligence whose whole-life
episodic memory becomes relevant continuity.

## Current reality

Already present and verified:

- Local browser chat through the configured local router. An older command-line
  chat path remains in code but is not a current product interface.
- Browser host and Origin checks permit local HTTP access and the exact HTTPS
  Tailscale Funnel at `th.tail674e3a.ts.net` without trusting other domains.
- Durable UTC event history with restart persistence.
- Lexical and embedding-assisted event recall.
- One normal conversation path for every accepted message; Sensitive mode and
  its automatic provider exclusions are retired.
- Separate relational and interior storage.
- Reflections, entity observations, and one waiting-message surface.
- Restored daily-life frontend with Notebook drawer and streaming chat.
- Background extraction/reflection with successful-pass completion records.
- Refreshed directive injected into foreground browser and terminal chat.
- Ordered talk-model failover: Qwen 3.8 Max, DeepSeek V4 Pro, then DeepSeek V4 Flash.
- Recall cues built from the recent eligible exchange plus the newest message.
- Resumed-session context has a tested bounded contract: selected-session tail,
  a two-event recent-life bridge on first resume, then global recall inside the
  existing eight-event ceiling.
- New text and voice events carry stable session IDs across restarts. New-chat
  boundaries start new IDs; legacy boundaries define deterministic sessions at
  read time without rewriting old event history.
- Browser session navigation lists, opens, continues, renames, archives, and
  restores chats. Control changes append new records; archive does not remove
  events from global recall. Keyboard focus, mobile touch targets, and semantic
  labels are built into the drawer.
- Browser text chat offers `Auto` or one configured model per session. Explicit
  choices never silently fall back; completed replies retain and show the model
  that actually answered. A clear Model error row can retry the same saved user
  event, including a one-attempt Auto override, without duplicating lived
  history.
- SQLite mirrors (relational and interior) dual-written alongside JSONL with fail-soft recovery.
- Conversational web search: Sage decides before replying when to search and
  stores query/source provenance separately from conversation events.
- Self-authored identity: heartbeat proposes identity claims from self-observation, Elliot ratifies/rejects via Notebook UI, ratified claims compose into system prompt.
- Autonomous metabolism: post-conversation gap scan, web exploration, digest reflection, and waiting-message reach. Each stage gates the next; silence is the default.
- An experimental `/call` path for native audio-to-audio conversation. Elliot
  verified the live microphone, spoken response, name recognition, and basic
  felt quality. The memory bridge now supplies ratified identity, recent
  context, and on-demand episodic recall; completed transcripts enter normal
  event history with voice provenance while audio remains unstored. Later
  corrections are linked without replacing original transcripts, and corrected
  wording drives recall. New calls and turns are grouped in a local review
  screen where Elliot can inspect and correct either side of the transcript.
  A separate `/call/split` latency trial sends held audio through Deepgram STT,
  Sage's normal streamed text path, and sentence-chunked Deepgram TTS. It shows
  STT, first-sentence, TTS, and total-to-audio timing while leaving direct Gemini Live
  unchanged as the baseline. Split voice now inherits the active chat session's
  model by default, with a persisted `Auto` or explicit voice override; reopened
  sessions keep voice turns in the selected session.
- Routed split-voice timing was rechecked twice per configured model: Qwen 3.8
  Max produced 0/2 valid streamed replies, DeepSeek V4 Pro measured 8.53s
  median, and DeepSeek V4 Flash measured 5.08s median to the first complete TTS
  audio blob. This backend probe excludes microphone capture and STT; the live
  screen reports those stages separately.
- The earlier `SAGE-021` integration-pass claim is withdrawn. Its 132 green
  tests included only two shallow deletion tests and did not establish safe
  deletion. Audit reproduced legacy ID drift, concurrent event loss, mirrors
  committing before authoritative files, and incomplete derived provenance.
- The deletion repair binds typed `DELETE` to an unchanged preview. It keeps
  survivor bytes and legacy identities, waits for in-flight provider work,
  stages survivor-only files and mirrors, and rolls a committed interruption
  forward before permitting further data access. Ambiguous, incomplete or
  shared derived provenance blocks deletion; archive remains available.

## Session deletion safety repair — 2026-09-10

Acceptance checks use temporary data only:

- Two successive legacy deletions retain surviving event/session IDs, exact
  original lines, transcript corrections and rebuilt mirror links.
- Stale previews and reentrant/concurrent writes cannot erase newly accepted
  history. Other processes cooperate through file locks; history reads remain
  available during streamed model replies.
- Process exits after preparation, the durable commit marker, each of ten
  authoritative survivor files and each of two mirrors recover on restart.
  Disk-sync, replacement and SQLite failures distinguish unchanged preparation
  from committed recovery; pending recovery returns HTTP 503 and pauses access.
- Read waiting messages, identity proposals/rulings, embeddings and voice-call
  groups are included. Surviving conversation events stay intact.
- Reflection provenance includes all six inputs; identity and metabolism carry
  their transitive event sources. Missing older provenance remains unknown,
  never guessed. Shared-source records block single-session deletion.
- Live calls started before a successful purge cannot save stale transcripts.
  Existing browser/provider context, other chats and external backups are not
  erased; no forensic storage-erasure guarantee is made.

Verification: `python3 -m unittest discover -s tests` passed **153 tests** in
64.165 seconds, including 23 deletion tests and the 14 crash-point subcases.
Python compilation, JavaScript syntax, the browser preview/confirmation
contract probe, and `git diff --check` passed. The AST project graph was updated.
The suite still emits a non-failing unclosed-SQLite-connection ResourceWarning.
Production repair commit `44952f9` was fast-forwarded to `/home/elliot/sage`
and pushed to `main`. After restart, `sage.service` was active, `/health`
returned `{"ok": true}`, all 17 sessions were available, and the updated
confirmation code was served. A read-only live deletion preview returned a
revision and 51 provenance blockers, correctly refusing unsupported scope.
Hashes of all eight existing authoritative JSONL files matched before and
after deployment. No live deletion request was sent; all destructive probes
used temporary data. This is evidence for the repaired deletion boundary,
not a claim that every backend behavior has been exhaustively proved.

## Backend integrity repair — 2026-09-12

A backend-wide audit found three failures outside the existing green suite.
The repair now recovers an interrupted final JSONL append before accepting the
next record across every append-only store. Complete no-newline records remain
intact. SQLite verification now compares complete mirror contents with fresh
temporary mirrors rebuilt from authoritative files, rather than trusting row
counts. Streamed model replies retain visible text when reasoning tags and
answer text share a provider chunk or split across chunk boundaries.

Three focused regressions failed before repair and pass afterward. Adversarial
probes covered every split point in a tagged stream and both valid and invalid
unterminated JSONL tails. The full suite passed **155 tests** in 65.510 seconds;
branch-aware source coverage measured 81%. Python compilation and
`git diff --check` passed. All destructive probes used temporary data.

Repair commit `2a67b95` was pushed and fast-forwarded into production. Live
verification exposed stale derived SQLite content despite equal row counts:
4 event model fields, 97 event/session links, and 6 session IDs. With Sage
stopped, both mirrors were rebuilt from authoritative JSONL; complete-content
verification then passed. After restart, `sage.service` was active and `/health`
returned `{"ok": true}`. Seven authoritative JSONL files stayed byte-identical.
Normal UI activity appended three session-control records to `events.jsonl`;
its original 190-line prefix still matched the pre-deploy hash exactly. Mirror
verification remained clean after those writes. No old event bytes were
changed. The existing non-failing unclosed-SQLite-connection ResourceWarning
remains.

## SAGE-021 six-batch integration — 2026-09-14

All 16 functional findings from the follow-up audit are now combined on the
integration branch. Shared-file conflicts were resolved by preserving both
contracts: corrected-content revisions remain attached to entity completion,
malformed or failed entity work remains retryable, search failure stays distinct
from a legitimate empty result, and source-less mirror rows retain revision data
without duplicating on repeated backfill.

The original audit suite now passes **31 of 31 tests** against the combined
checkout. Its concurrent-retry probe was updated to assert the repaired contract:
the second request is rejected before a second provider call. The full permanent
suite passes **197 tests** in 77.915 seconds. Python compilation and
`git diff --check` pass. Tests use temporary data and fake providers. Integration
commit `0b0a539` was pushed, fast-forwarded to production `main`, and deployed.
`sage.service` is active. Opening the lazy derived mirrors applied four
`content_revision` columns; complete-content verification against fresh
temporary rebuilds then passed. All eight authoritative JSONL hashes and their
648 total lines stayed unchanged. Background providers were already returning
no reply before deployment and still do; repaired passes leave that work
retryable instead of falsely completing it.

## Transcript correction consistency repair — 2026-09-14

Corrected voice wording now has a deterministic content revision across
embeddings and background understanding. Recall accepts only a vector matching
the effective wording; a failed correction embedding therefore cannot keep the
old meaning active. Repeating the correction can retry embedding without
rewriting the original transcript. Entity extraction and reflection completion
bind to the effective event or dialogue-window revision, so a later correction
appends revised source-linked observations and reflections while keeping older
derived records available. Revised self-reflections remain eligible for a new
identity proposal; existing proposals and Elliot's rulings are not silently
retired.

SQLite mirrors store the same revision metadata, add the columns to existing
databases on open, and reproduce the latest completion/vector state during a
fresh rebuild. Nine temporary-data regressions cover correction before and
after extraction, repeated and successive corrections, failed then successful
re-embedding, out-of-order stale vectors, restart idempotence, reflection and
identity consumption, mirror migration, and exact rebuild. The full normal
suite passed **164 tests** in 65.074 seconds. Python compilation and
`git diff --check` passed. The existing non-failing unclosed-SQLite-connection
ResourceWarning remains. This isolated repair has not been integrated, deployed,
or tested with the other five SAGE-021 repair branches.

## Provider and search response repair — 2026-09-13

Malformed router containers now use normal model failure and fallback behavior
instead of raising. Blank streams and unfinished reasoning-only replies cannot
be saved as answers. Recognized usage-only stream metadata no longer discards a
completed answer; malformed or truncated streams still remain incomplete, and
explicit model requests still never fall back.

Truncated and malformed search responses now fail soft, allowing ordinary chat
to continue. Search returns a list-compatible result carrying `failed=True` for
provider or schema failure and `failed=False` for a legitimate empty result.
This gives the separate background-completion repair a shared signal without
changing metabolism behavior in this batch.

All seven assigned SAGE-021 routing probes pass against this worktree. Permanent
regressions also cover invalid usage metadata type and placement, whitespace,
visible output before later failure, completed versus truncated streams, search
failure versus empty results, browser continuation, and accepted-user
preservation. Full-suite evidence is **168 tests passed**; the known non-failing
unclosed-SQLite `ResourceWarning` remains.

## SAGE-027 repair batch — 2026-09-14

Repeated relational backfill now mirrors each source-less legacy entity
observation once while preserving distinct original lines, including identical
records. Overlapping identity workers may call the model concurrently, but a
short final data lock revalidates evidence before one proposal consumes it.
Missing `Content-Length` now returns 411 from JSON and raw-body readers, and
invalid UTF-8 JSON returns 400 instead of closing the connection.

The four assigned SAGE-021 audit probes pass against this checkout. Five
permanent regressions cover identical source-less records and unchanged JSONL
bytes, deterministic worker overlap, both request readers, and invalid UTF-8.
`python3 -m unittest discover -s tests` passed **160 tests** in 68.462 seconds.
All tests used temporary data and fake or local-only providers. This repair is
branch-only; combined integration and deployment remain pending.

## Session, retry, and history repair — 2026-09-14

SAGE-022 repairs the first three functional findings from the later SAGE-021
audit. A foreground turn now keeps the session context captured when its user
event is accepted, even if another chat is opened while search is pending.
Concurrent retries of one saved user event use one short-lived ownership claim,
so only one provider answer is generated and saved; a failed attempt releases
the claim for another retry. Modern assistant replies that literally resemble
the retired search-metadata format remain visible and recallable, while proven
untagged legacy search rows remain outside dialogue.

All four focused regressions pass, including failed-retry recovery. Six adjacent
session, model-selection, retry, recall, and legacy-history checks pass. The full
normal suite passed **159 tests** in 65.040 seconds. Python compilation,
`git diff --check`, and `graphify update .` passed; Graphify rebuilt 846 nodes
and 1,935 edges. Tests used temporary data and fake providers. This evidence
covers S21-01 through S21-03 only; it does not claim the other audit batches are
repaired or the combined parallel branches are integrated.

These are foundations, not proof that Sage already feels like a JARVIS-like
personal intelligence.

## Waiting-message durability repair — 2026-09-14

The isolated SAGE-023 repair branch fixes audit finding S21-04. Waiting-message
revision and acknowledgement now write and sync a complete temporary sibling
before atomically replacing the authoritative JSON file. Failed writes or
replacements leave the previous complete message intact; failed temporary files
are removed. Acknowledgement updates the derived mirror only after the
authoritative replacement succeeds.

Four permanent regressions cover partial-write and replacement failures for
both operations, restart reads, successful revision and acknowledgement, and
the existing single waiting-message behavior. Focused and adjacent checks
passed. The full suite passed **159 tests** in 64.905 seconds. The AST project
graph was rebuilt. This branch has not been integrated or deployed; no lived
data was touched.

## SAGE-025 background retry repair — 2026-09-14

The S21-07 and S21-08 repair branch now records metabolism completion only
after a successful pipeline outcome. Provider, parsing, search, digest, and
reach failures remain retryable; valid no-gap, no-result, and declined-reach
outcomes still complete. Retries reuse already-written gap, search, digest, and
reach results, avoiding duplicate derived records while preserving provenance.

Entity extraction now validates the complete JSON container and every required
string field before writing any observation or completion. Wrong containers and
malformed list items therefore leave the source event retryable; `[]` remains a
successful no-entity result.

Twelve focused regression and adjacent checks passed in 6.026 seconds. The full
normal suite passed **162 tests** in 68.350 seconds. Python compilation and
`git diff --check` passed. Tests used temporary data and fake providers only.
Integration with SAGE-026 must preserve one search boundary: successful zero
results are plain `[]`; a fail-soft parser or transport failure remains equal to
`[]` for existing callers but carries `failed=True` so metabolism can retry it.
This is branch evidence only; no sibling repair was merged or deployed.

## SAGE-021 functional audit — 2026-09-13

The follow-up audit found **16 reproducible functional defects** beyond the
earlier three backend repairs. They affect session context, concurrent retries,
history visibility, waiting-message durability, transcript derivatives,
background completion, provider/search parsing, mirror idempotency, overlapping
identity workers and request error reporting. They are documented with precise
conditions and runnable probes in [SAGE_021_AUDIT.md](SAGE_021_AUDIT.md).

Application code remains at `9587f37`; this is an audit handoff, not a repair.
The existing suite passed **155 tests** in 65.601 seconds. A separate audit
suite ran **31 methods**: 11 passed and 20 reproduced defects; subcases yielded
22 assertion failures and 4 uncaught application exceptions. Coverage was
81.43% combined with branch measurement enabled; actual branch coverage was
77.24%. The existing SQLite ResourceWarning remains unclassified.

The immediate engineering work is choosing and repairing one documented defect
group. Felt continuity remains the product outcome below.

## Active outcome — felt continuity

Sage's remembered history influences replies naturally in ordinary, non-crisis
conversations. Related events can be grouped or summarized without erasing
their sources.

## Acceptance evidence — felt continuity

- Relevant older moments influence replies without sounding like search.
- Contradictory events remain available without forced resolution.
- Related moments can be grouped without replacing their original events.
- Ordinary conversation with Elliot provides a felt test, not only technical
  retrieval scores.

## Explicitly not active yet

- Broad autonomous actions beyond web search.
- External notifications.
- Production voice or ambient interfaces.
- A belief model or belief-edit workflow.
- A graph or current-state replacement for event memory.
- More background activity merely to appear alive.
