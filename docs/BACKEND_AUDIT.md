# Backend audit state

Updated: 2026-09-12 (WIB)

## Baseline

- Audit branch: `codex/session-6-purge`
- Baseline revision: `b6f3938817a961001801ac75174df4b53cc9e980`
- Worktree and production were clean and at the same revision.
- Full suite: 153 tests passed in 64.729 seconds.
- Branch-aware source coverage: 79% overall.
- Production `sage.service`: active; `/health` returned `{"ok": true}`.
- Audit probes used temporary data only. `~/sage_data` was not touched.

## Confirmed faults

1. **Authoritative JSONL crash tail** — `EventStore` ignores an incomplete final line while reading, but its next append writes directly after that fragment. The joined line becomes invalid JSON and history becomes unreadable.
2. **False-clean SQLite verification** — mirror verification compares row counts only. A temporary mirrored event changed from `truth` to `WRONG` still passed verification. Existing rows with matching IDs are not repaired by backfill.
3. **Dropped streamed reply** — router streaming loses visible output when `<think>...</think>` and answer text share one provider chunk. Probe input `<think>secret</think>answer` emitted no chunks.

## Repair outcome

Implemented, verified, and deployed in repair commit `2a67b95`.

- One shared durable JSONL writer now removes only an invalid, unterminated
  crash fragment before appending. A complete final record without a newline
  is retained. All Sage JSONL writers use this path.
- Mirror verification now builds fresh temporary mirrors from authoritative
  files and compares every mirrored field. It detects changed, missing, or
  extra rows while ignoring meaningless SQLite surrogate IDs.
- Streaming reasoning filtering now handles opening and closing tags in one
  chunk or split across arbitrary chunk boundaries without losing visible
  answer text.

Verification completed:

- Three focused regression tests failed before fixes and passed afterward.
- Adversarial probes passed across every split point in
  `visible<think>secret</think>answer`, plus invalid and valid no-newline JSONL
  tails.
- Foundation suite: 101 tests passed.
- Mirror suite: 22 tests passed.
- Full suite: 155 tests passed in 65.510 seconds.
- Branch-aware source coverage: 81% overall.
- Python compilation and `git diff --check` passed.
- Changed paths received a separate correctness and scope review.
- `graphify update .` refreshed the code graph.
- Existing non-failing unclosed-SQLite `ResourceWarning` remains.

Live read-only verification found equal row counts but stale relational mirror
content: 4 event model fields, 97 event-to-session links, and 6 session IDs
differ from authoritative JSONL. No conversation text was printed. Production
deployment stopped Sage, rebuilt only `relational.db` and `interior.db` from
JSONL, and passed exact content verification afterward.

Deployment verification completed:

- Production fast-forwarded to `2a67b95`; `sage.service` is active and `/health`
  returns `{"ok": true}`.
- Seven authoritative JSONL files remained byte-identical.
- Normal UI activity appended three valid session-control records after restart.
  The original 190-line `events.jsonl` prefix remains byte-identical to its
  pre-deploy hash; no old event bytes changed.
- Exact mirror verification remains clean after those three append-only writes.
- Elliot's uncommitted `AGENTS.md`, untracked `CLAUDE.md`, and shared
  `.agent/HANDOFF.md` were preserved and excluded from milestone commits.
