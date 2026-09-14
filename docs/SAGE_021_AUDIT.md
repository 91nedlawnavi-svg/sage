# SAGE-021 functional backend audit

Completed: 2026-09-13 WIB. Baseline: `9587f372d394f78840c7cd7ef9372a6494b741be`.

**16 reproducible functional faults remain after the earlier backend repairs.**
These are failures in controlled probes, not claims that all have occurred in
Elliot's history. No application fixes are included in this audit.

Resolution update, 2026-09-14 WIB: all 16 findings are repaired on the combined
integration branch. The updated audit suite passes 31 of 31 tests and the full
permanent suite passes 197 tests. Integration commit `0b0a539` is deployed on
production `main`; derived mirror content verification passes and authoritative
JSONL stayed byte-identical. The findings below retain their original baseline
evidence.

## Evidence and scope

- Existing suite: **155 tests passed in 65.601 seconds** at the baseline.
- Coverage measured with branches enabled: **81.43% combined**, **82.95%
  statements**, **77.24% branches**. The earlier shorthand "81% branch
  coverage" was inaccurate. 503 statements and 244 branch destinations were
  not exercised by the existing suite.
- New audit probes: **31 methods, 11 passing, 20 unsuccessful**. Unittest
  reports **22 assertion failures and 4 errors**, because background tests
  contain multiple failing subcases. The four errors are uncaught application
  exceptions and are part of the reproduced defects. Final run: 2.239 seconds.
- Application files under `src/`, `launch.py`, and existing tests remain at
  baseline. Probes use fresh temporary stores, deterministic coordination,
  fake model responses, and isolated request handlers or test servers.
- Review covered storage, mirrors, deletion/recovery, conversation context,
  retry, response parsing, recall, corrections, background completion,
  provenance, identity proposals and request error reporting. This is a
  bounded audit; it cannot establish that no other faults exist.

The probes are outside `tests/` intentionally. They assert required behavior
and currently fail where the audit found defects. They do not turn the normal
regression suite red merely by being checked in.

```sh
python3 -m unittest discover -s tests
python3 -m unittest discover -s tools/audit_sage021 -v
```

Run either command from this checkout. The second command is expected to exit
with status 1 until the findings are fixed. Run one area with `-p
test_routing.py`, `test_background.py`, `test_storage.py`, or `test_http.py`.

## Findings

P2 means a concrete functional defect worth fixing in a normal milestone.
P3 means a lower-impact error-reporting defect. Ordering reflects suggested
repair order, not measured frequency in production.

### S21-01 — P2: switching chats changes a pending reply's context

`src/web.py:623`, `src/web.py:676`, `src/sage.py:51`.

For an ordinary active session, `resumed_session_history()` returns `None`.
After search finishes, context assembly then reads whichever session is active
at that later moment. A switch during search removes the original orchard
conversation from the test prompt and substitutes an ocean conversation. The
answer is still stored in the orchard session. Global recall across sessions
is intentional; losing the selected session's guaranteed tail is the defect.

Probe: `RoutingAudit.test_switch_during_search_keeps_original_session_tail`.
Repair direction: capture the accepted turn's session and required context as
one consistent operation; use that snapshot throughout the reply.

### S21-02 — P2: concurrent retries save two answers for one user turn

`src/web.py:608`, `src/web.py:920`.

Two retries can both pass the latest-unanswered check before either saves an
answer. A deterministic barrier produces one user event and two assistant
events. Normal per-file locks preserve both writes but do not claim ownership
of the retry. Multiple tabs or overlapping requests can trigger this.

Probe: `RoutingAudit.test_concurrent_retries_generate_only_one_answer`.
Repair direction: claim and revalidate retry ownership by saved user-event ID;
preserve the existing single saved user event.

### S21-03 — P2: a legacy filter hides valid new assistant events

`src/events.py:631`, `src/events.py:1065`.

An assistant answer beginning `[Web search: ` and containing `\nSources:` is
classified as an old synthetic search record regardless of its modern event
ID, session, source or model. A legitimate answer demonstrating that literal
format disappears from history and recall. Its original JSONL bytes survive.

Probe: `RoutingAudit.test_new_assistant_search_shaped_text_remains_history`.
Repair direction: constrain legacy recognition to proven legacy records;
content alone must not hide newly accepted replies.

### S21-04 — P2: failed waiting-message writes destroy the saved note

`src/interior.py:220`, `src/interior.py:230`.

Revision opens the authoritative JSON file with truncation before writing.
A partial write followed by disk-full leaves the original unreadable.
Acknowledgement truncates in place too; on write failure its exception handler
can delete the original file. This is separate from the repaired JSONL append
path and is confirmed with injected write failures.

Probes: `StorageAuditTests.test_failed_waiting_revision_preserves_previous_message`
and `test_failed_waiting_acknowledgement_preserves_existing_record`.
Repair direction: write a complete replacement to a temporary sibling, sync,
then replace; an unsuccessful write must retain the prior record.

### S21-05 — P2: correction embedding failure leaves old meaning active

`src/events.py:238`, `src/events.py:700`, `src/events.py:726`.

Correcting a voice transcript changes displayed and lexical wording. If the
embedder is unavailable, the old vector remains associated with that event ID.
In the probe, an ocean query still retrieves a transcript corrected from
"seaside holiday" to "violet garden" through the obsolete vector. Successful
re-embedding correctly stops that match.

Probe: `RoutingAudit.test_correction_with_failed_embedding_cannot_recall_old_meaning`.
Repair direction: track which content revision a vector describes, exclude
obsolete vectors, and allow replacement to retry without rewriting history.

### S21-06 — P2: processed voice corrections never revise extracted facts

`src/events.py:225`, `src/heartbeat.py:136`.

Entity processing is marked complete using the original event ID. A later
correction keeps that ID, so the corrected wording is never processed. The
probe extracts "Mara grows roses", corrects the transcript to "Mara grows
rice", and cannot produce the corrected observation on another pass. A
correction made before initial processing works. The settled correction
contract in `docs/DECISIONS.md` says corrected wording guides background
understanding as well as recall.

Probe: `BackgroundAudit.test_voice_correction_reaches_already_processed_entity_input`.
Repair direction: completion must account for input revision; preserve older
observations and append source-linked revisions. Reflections and identity
derived from corrected material also need a scoped review; this probe proves
the entity failure specifically.

### S21-07 — P2: failed metabolism is permanently recorded as completed

`src/heartbeat.py:255`, `src/heartbeat.py:265`, `src/metabolism.py:47`,
`src/metabolism.py:244`.

Real stages convert failures to empty lists or `None`. The outer pass sees a
normal return and writes completion, preventing retries for that user event.
Six cases reproduce: gap provider failure, provider exception, invalid gap
JSON, failed search, failed digest and failed reach. A valid empty gap result
is correctly completed by a passing control. Failure and legitimate silence
currently share the same result shape.

Probe: `BackgroundAudit.test_real_metabolism_failures_remain_retryable`.
Repair direction: distinguish successful silence from failed work and record
completion only for a successful outcome; keep retry side effects idempotent.

### S21-08 — P2: malformed entity output silently consumes its input

`src/heartbeat.py:155`–`168`.

Valid JSON with the wrong structure, such as `{"entities":[]}` or a list of
objects without the required fields, writes an entity completion without an
observation. It is never retried. Valid `[]` correctly means no entities and
is covered by a passing control.

Probe: `BackgroundAudit.test_invalid_entity_response_remains_retryable`.
Repair direction: validate the entire response shape before acknowledging
successful extraction; malformed output must remain retryable.

### S21-09 — P2: malformed router responses bypass fallback

`src/router.py:128`, `src/router.py:206`.

`message:null` in a nonstream response or `delta:null` in a streamed response
raises uncaught `AttributeError`. The next configured model is never attempted,
even when no visible answer has been emitted. In browser chat this can leave
an accepted user event with an aborted response instead of the promised model
error or fallback.

Probes: `RoutingAudit.test_malformed_chat_schema_falls_back` and
`test_malformed_stream_schema_falls_back_before_visible_text`.
Repair direction: validate response containers before accessing their fields
and route malformed results through the normal failure contract.

### S21-10 — P2: whitespace-only streams count as answers

`src/router.py:235`, `src/web.py:908`.

A stream of spaces, newline and tab followed by `[DONE]` is treated as a
successful reply and saved. The blank assistant event also makes the user
turn ineligible for the unanswered-message retry. The nonstream path already
rejects whitespace-only content.

Probe: `RoutingAudit.test_whitespace_stream_is_failure_and_saves_no_assistant`.
Repair direction: require nonblank visible content before completion/save.

### S21-11 — P2: unfinished reasoning-only replies count as answers

`src/router.py:24`, `src/router.py:141`.

A nonstream response containing only `<think>private reasoning` survives the
reasoning-stripper because the closing tag is absent. It is returned as
success and bypasses a valid fallback response. Nonstream background callers
can consequently persist unfinished reasoning as derived text. This directly
contradicts the configured failure rule for reasoning-only replies.

Probe: `RoutingAudit.test_unclosed_reasoning_only_chat_falls_back`.
Repair direction: reject incomplete reasoning blocks using a consistent
visible-answer validation rule.

### S21-12 — P2: stream usage metadata discards a complete answer

`src/router.py:207`.

An answer followed by `{"choices":[],"usage":...}` and then `[DONE]` returns
early at the empty choices list. Text was emitted, but completion is lost;
callers report model failure and save no answer. This is a reproduced parser
compatibility fault. The audit does not establish whether the currently
configured gateway emits usage-only chunks.

Probe: `RoutingAudit.test_stream_usage_metadata_does_not_discard_complete_answer`.
Repair direction: handle recognized metadata-only packets independently of
answer deltas while retaining truncation detection.

### S21-13 — P2: search failures can abort ordinary conversation

`src/search.py:34`–`44`, `src/web.py:648`.

A truncated response body raises uncaught `IncompleteRead`. A response with
`results:[null]` raises `AttributeError`. Both escape the search helper and
abort a chat after its user event is accepted, instead of reporting a search
failure and continuing the response path.

Probes: `RoutingAudit.test_truncated_search_fails_soft` and
`test_malformed_search_schema_fails_soft`.
Repair direction: validate search response structure and handle incomplete
reads through the existing empty/error result behavior.

### S21-14 — P2: repeated backfill duplicates source-less observations

`src/database.py:85`, `src/mirror_rebuild.py:154`.

Legacy entity observations may have no `source_event_id`. SQLite's uniqueness
constraint permits multiple NULL values, so `INSERT OR IGNORE` does not make
backfill idempotent for them. One authoritative record yields one mirror row
on the first run and two on the second; subsequent runs keep adding copies.

Probe: `StorageAuditTests.test_backfill_legacy_entity_without_source_is_idempotent`.
Repair direction: establish stable derived identity for source-less records
without collapsing distinct original observations or changing their bytes.

### S21-15 — P2: overlapping background workers duplicate identity proposals

`src/heartbeat.py:209`–`237`.

Two workers can select the same unconsumed self-reflection, both ask the model,
and both append proposals using identical evidence. The shared activity lease
permits both operations; there is no final consumption check. The deterministic
probe uses two workers and a barrier. This finding requires overlapping workers;
the ordinary single-worker service does not trigger it by itself.

Probe: `BackgroundAudit.test_concurrent_identity_proposals_consume_evidence_once`.
Repair direction: revalidate and consume evidence under the short data lock
before appending a new proposal.

### S21-16 — P3: malformed request bodies bypass intended error responses

`src/web.py:832`, `src/web.py:840`, `src/web.py:851`.

A missing Content-Length produces `int(None)` and uncaught `TypeError`, rather
than the implemented 411 response. Invalid UTF-8 produces uncaught
`UnicodeDecodeError`, rather than a 400 response. Both probes receive a closed
response without the intended explanation. Ordinary invalid JSON receives
400 in a passing control. This finding concerns error reporting only.

Probes: `HttpAuditTests.test_missing_content_length_returns_client_error` and
`test_invalid_utf8_returns_client_error`.
Repair direction: handle absent lengths and decoding errors in the shared
request readers.

## Verified controls and remaining uncertainty

- Every tested byte cut of a multibyte UTF-8 JSONL tail preserves complete
  records and accepts the next append correctly.
- Four concurrent writer processes preserve all 121 expected events.
- Existing deletion tests, including the 14 crash-point subcases, passed
  within the baseline suite. No new deletion-recovery fault was confirmed.
- Valid empty background results complete once; corrections made before
  extraction use corrected wording; transitive provenance retains known
  sources and does not label unknown inputs complete.
- Explicit model failure never falls back in the control; truncated streams
  save no partial assistant reply; successful correction embeddings replace
  old meaning.
- Display interruption preserves the accepted user event. An earlier draft
  probe called its missing assistant event "lost complete reply"; review
  rejected that claim because the completion marker had not been consumed.
  Whether abandoned requests should finish in the background is a product
  decision, not a confirmed defect from this probe.
- `tools/backfill_sqlite.py --verify` **backfills before checking**. It is a
  maintenance command, not a read-only inspection command. Earlier status
  descriptions calling it read-only were inaccurate. This audit used it only
  on synthetic data. `mirror_rebuild.verify` performs the separate comparison.
- The existing unclosed-SQLite `ResourceWarning` reproduced in the baseline.
  Its resource-lifetime cause was not established here; calling it harmless
  would exceed the evidence.
- Actual provider payload frequency, live microphone/browser behavior,
  physical power-loss behavior, every possible process interleaving, and
  large-history performance remain unmeasured. Existing test coverage is not
  a measure of all possible behavior.

## Proposed repair sequence

1. Session/retry correctness and newly hidden events: S21-01 through S21-03.
2. Waiting-message durability: S21-04, with write-failure and restart checks.
3. Correction consistency: S21-05 and S21-06, preserving original records.
4. Honest background completion: S21-07 and S21-08.
5. Provider and search response handling: S21-09 through S21-13.
6. Mirror idempotency, overlapping workers and error reporting: S21-14 through
   S21-16.

Each repair should convert its failing audit probe into a permanent regression
test, run the normal suite, and leave a checkpoint for either development agent.
These are proposed follow-up milestones; no repair is claimed complete here.
