# Self-Authored Identity — Design

**Status:** Reviewed 2026-09-02. Automatic recurrence detection rejected on measured
evidence; **ratification model approved by Elliot**. Reflection-stream change is
implemented; identity storage, composition and surfaces are still design.
**Authority:** Elliot — "She BUILDS her own identity overtime, over conversations, experiences"

## Problem

Sage's identity is static: `directive.txt` is a fixed seed that never changes regardless
of how many conversations she has. Elliot wants her identity to grow from experience,
within guardrails that keep drift from becoming corruption.

## Design Principles

1. **Seed is permanent.** `directive.txt` stays git-tracked, outside lived memory, never
   written by Sage. It is Elliot's voice setting initial conditions. Wiping lived memory
   returns her to the seed, not to nothing.

2. **Growth is ratified, not declared.** Sage proposes; Elliot approves. A candidate
   becomes part of her identity when he says so, and not before. This replaces the
   original "observed across 2+ conversations" rule — see *Why not automatic recurrence*.

3. **Composition, not replacement.** Effective identity is `seed + ratified entries`,
   composed at load time. Ratified entries extend the seed; they never override it.

4. **Drift is visible by construction.** Nothing enters her identity without passing
   through Elliot's review, so the accumulation surface is the same surface as the
   approval surface.

## Why not automatic recurrence

The first draft of this design earned entries automatically: compare each new reflection
against reflections from other conversations, promote a claim when semantic similarity
exceeded 0.85 across 2+ separate conversations. Measured against the live data on
2026-09-02, that mechanism never fires.

All 7 reflections then in `~/sage_data/interior/reflections.jsonl` were embedded on the
local embedder (1024-dim) and scored pairwise, 21 pairs:

- max pairwise cosine **0.697**, mean 0.531, min 0.343
- pairs above the specified 0.85 threshold: **0 of 21**
- the one genuine recurrence in the data — "I just repeated the exact phrase I promised
  to drop" and "I keep defaulting to the same opener even after Elliot called it out" —
  scored **0.697**, and both sat inside the same conversation bucket, so the
  2-different-conversations gate rejected it as well

The design's stated fear was a detector too loose to trust. The measured failure is the
opposite, on both axes at once. A real separation does exist on this embedder — generic
pairs land 0.34–0.62 and the true positive at 0.70 — but it is centred near 0.65, not
0.85, and tuning a threshold against a 7-reflection sample would be fitting noise.

Ratification removes the need for a threshold at all. It also deletes the reflection
embedding store, the mirror table for it, the rate limiter, and most of the mechanical
resemblance to the promotion desk killed on 2026-08-02.

## The loop ratification closes

Promoted world-facts had an external referent: Elliot's life could contradict them.
Identity claims have none. Evidence is her own reflections, reflections are generated
from her own output, and a ratified entry is injected back into the prompt that generates
that output.

Left automatic, that is positive feedback with no damping: an entry saying "I tend to
push back" is read by a free-tier model as an instruction to push back, the next
reflection notices pushing back, the entry is reinforced. The evidence requirement does
not damp it, because the evidence is inside the loop. Nothing outside can falsify the
claim.

Elliot's judgment is the external referent. That is the whole reason ratification is the
mechanism and not merely a convenience.

The honest cost: if he stops reviewing, identity stops growing. That failure mode is
visible and recoverable. Silent drift is neither.

## The candidate stream — IMPLEMENTED 2026-09-02

Measured problem: of the first 7 reflections, 4 were observations about Elliot ("He's
testing the edges of his own reality", "The /sensitive tag feels like a shield you're
holding up") and 3 about Sage's own behaviour. The old prompt asked for "current context,
rhythm, or questions", so most of the stream was not identity material at all.

The reflection prompt now asks for the ordinary reflection, and — only when the notable
thing in the exchange is something Sage herself did — for that instead, prefixed `SELF:`.
`parse_reflection()` in `src/heartbeat.py` strips the marker and stores `category="self"`;
everything else stores `category="general"`. One model call per beat, as before. No
classifier, no second pass, no schema change: `category` was already on the `Reflection`
TypedDict, already written to JSONL, already mirrored.

Three evidence bullets gate the marker — a stated intention she then contradicted, the
same move of hers appearing twice in the window, or a habit of hers stated outright in the
transcript — and the prompt disqualifies feeling-claims ("I feel I could be more present")
explicitly, because those point at no behaviour.

**Abstention is the design, not a shortfall.** Most exchanges hold no notable
self-pattern. Marking a routine exchange manufactures an identity claim out of nothing,
which is the failure mode that matters here.

Measured on the production alias `xk/qwen/qwen3.8-max:free` across 22 live calls: correct
marking on a window containing a real self-pattern, correct abstention on a window with
none, and no format drift. Two known limits, both recorded rather than tuned away:

- Only the first bullet (stated intention, then contradicted) has been observed firing.
  Repetition-in-window and stated-habit did not fire on any real window, so the `self`
  stream will be sparse. Watch the live rate; do not re-tune against three windows.
- Output diversity is low and prompt-independent — `RouterClient` sends no temperature, so
  consecutive beats over similar windows produce near-identical entries.

Candidate identity entries are the `category == "self"` reflections. That is the whole
selection mechanism: no embeddings, no similarity, no classification call.

## Architecture

### Seed: `directive.txt`

Repo root, git-tracked, 79 lines. Read by `sage.py:load_directive()`, injected as the
system message by `build_router_messages()`. Unchanged by this design.

### Entries: `~/sage_data/interior/identity.jsonl`

Append-only, inside lived memory, so wiping lived memory returns her to the seed.

A **proposal** record:

```json
{
  "kind": "proposal",
  "id": "uuid",
  "claim": "I restate an intention and then contradict it inside the same exchange",
  "evidence": ["reflection-uuid-1", "reflection-uuid-2"],
  "said_at": "2026-09-02T12:00:00Z"
}
```

A **ruling** record, appended when Elliot acts:

```json
{"kind": "ruling", "target_id": "uuid", "verdict": "ratified", "said_at": "..."}
```

`verdict` is `ratified`, `rejected`, or `retired` (a previously ratified entry Elliot no
longer stands behind). Records are folded in file order, last ruling wins. Effective
identity is every proposal whose latest ruling is `ratified`.

**No deletes, no tombstone table, no row mutation.** This is the privacy-record shape
already in the codebase — `events.py` appends a record naming an earlier one by id, folds
in file order so the last wins, and applies the fold at read time while the original stays
intact (`events.py:149-168`, fold at `events.py:518-524`, read-time application at
`events.py:242-263`). Deletion has zero precedent in this repo and sits against
`INVARIANTS.md:20` and `:46`; the fold does the same work without breaking either.

Fields deliberately absent: no `category`. The four-way enum from the first draft
(`voice`/`stance`/`relationship`/`self-knowledge`) is dropped — classification is exactly
what small models do badly, the value was never established, and a `CHECK` constraint on
it is actively unsafe here (see the mirror note below). A flat list is cheaper and
reversible; add categories later if the list gets long enough to need them.

### Mirror: `identity_entries` in `interior.db`

Add the DDL to `INTERIOR_SCHEMA` (`database.py:82-100`). `Database.conn` runs
`executescript` on every connect (`database.py:123`), so a new `CREATE TABLE IF NOT
EXISTS` appears in the existing `interior.db` on next start — no migration needed. The
name must not collide with any relational table or `tests/test_mirror.py:211-220` fails;
`identity_entries` is clear.

Three constraints on the mirror write, all load-bearing:

- **No `CHECK` constraints.** The copied `INSERT OR IGNORE` pattern silently swallows
  `CHECK` and `NOT NULL` violations — 0 rows inserted, no exception raised. A constraint
  that fails invisibly is worse than no constraint.
- `evidence` is an array, so `json.dumps` on write and `json.loads` with
  `JSONDecodeError`/`TypeError` tolerance on read. Copy `database.py:161-183`.
- Adding a **column** later would be the first migration in this codebase (no
  `ALTER TABLE`, no `user_version` anywhere). Get the columns right the first time.

`tools/backfill_sqlite.py:214-218` equates table rows with JSONL lines and `main()` exits
1 on mismatch, so the verify expectation must count proposals only, not ruling lines —
otherwise `--verify` reports a false mismatch forever.

### Composition at load

`load_directive()` gains an optional identity path. Ratified claims are appended after the
seed under a framing that marks them as observations:

```
[seed from directive.txt]

---

Things I have noticed about myself, and Elliot has confirmed:

- I restate an intention and then contradict it inside the same exchange
```

The seed wins any conflict. Composition must **fail soft**: `_read_jsonl` re-raises on any
malformed non-final line (`interior.py:113-117`), and one hand-edited line in
`identity.jsonl` would otherwise break startup. Catch, log, compose the seed alone.

Cap the composed block at 10 entries, newest first. `directive.txt` is already ~4000
characters injected on every turn; the entries ride along on a free-tier budget.

## Proposal generation

A heartbeat pass, gated the same way every other pass is: it does nothing unless there is
an unproposed `category == "self"` reflection.

1. Collect `self` reflections not yet named in any proposal's `evidence`.
2. If there are none, stop. This is the resting state and it costs no model call.
3. Otherwise one model call turns them into a single first-person claim, with those
   reflection ids as evidence, appended as a `proposal`.

Rate limiting is not needed: proposals cannot enter identity on their own, and Elliot's
review is the throughput limit. Note that `append_reflection` returns the pre-existing
record on a dedup hit (`interior.py:66`), so a caller cannot tell whether a write actually
happened — compare ids rather than assuming.

Deliberately not built: retroactive mining of existing reflections. If Elliot wants the
current 8 reflections considered, that is a one-off tool run, not a startup behaviour.

## Surfaces

### `GET /api/identity`

Returns `{"identity": [...]}`. **The response key must equal the tab name** — `app.js:229`
does `data[tab]`, so the first draft's `{seed_path, seed_hash, entries, ...}` shape would
render "No identity recorded yet." forever. Seed metadata, if wanted, goes inside the list
payload or a separate endpoint.

Each item carries the claim, evidence reflection ids, `said_at`, and folded status
(`proposed`/`ratified`/`rejected`/`retired`). Read through `InteriorStore` from JSONL, never
by querying SQL: `web.py:37` and `web.py:345` construct `InteriorStore` with **no mirror**,
so `_mirror is None` on the whole web path.

### `POST /api/identity/<id>/ratify` and `/reject`

Follow `_privacy_override` (`web.py:199-215`) and `_privacy_target` (`web.py:239-246`):
parse the id after the exact-path branches, harden it (reject empty, reject `/` after
`unquote`), no request body — `app.js:128`'s bodiless `fetch` sends no Content-Length, so
calling `_json_body` would answer 411/415. Unknown id returns JSON 404 like `web.py:213`.

### Notebook drawer tab

Three edits: a `data-tab="identity"` button in `index.html:59-63` (the tab list is
snapshotted once at `app.js:14`, so it must be in the HTML), an entry in the endpoint
ternary at `app.js:225`, and a render branch at `app.js:234-256`. **Both chains fall
through to entities** — skip either edit and the Identity tab silently shows entity
observations with a 200 OK.

Ratify/Reject buttons are the first interactive controls inside drawer content. Two things
follow: event delegation on `#drawerContent` with the id in a data attribute, then reload
the tab; and the focus trap at `app.js:162` must be extended, because it currently jumps
forward-Tab from the active tab straight to the close button, leaving anything rendered
inside the drawer keyboard-unreachable.

### Security note — pre-existing, and this design widens it

There is no authentication, session, cookie, CSRF token, or rate limit on any endpoint.
GET is gated only by a Host-header check (`web.py:287-298`); POST adds an Origin check that
**passes when Origin is absent** (`web.py:300-302`). The listener binds `0.0.0.0`
(`web.py:346`).

Today the only state-changing POSTs are privacy overrides and chat clears. Ratify/reject
adds a route by which anything on the LAN can write Sage's identity. That is a real
widening of an existing hole, not a new class of problem, and it is Elliot's call whether
to close it first. The cheapest fix is binding to `127.0.0.1` unless a LAN bind is wanted;
the correct fix is a shared-secret header on state-changing routes.

## What this design does NOT do

- No modification of `directive.txt`. The seed is Elliot's alone.
- No belief storage. Beliefs stay computed at recall.
- No reflection embeddings, no similarity threshold, no promotion arithmetic.
- No deletes, no `ALTER TABLE`, no mutation of a written record.
- No entity-graph integration. Identity is interior-only.
- No template on `beliefs_path` (`interior.py:48`) — it is the dead shape the memory-aspects
  doc settled against, and `test_foundation.py:730` asserts no write path may be added.

## Implementation order

1. `identity.jsonl` + `InteriorStore` append/list/fold + mirror table + backfill + tests.
2. Proposal pass in the heartbeat, gated on unproposed `self` reflections.
3. Composition in `load_directive()`, fail-soft, capped.
4. `GET /api/identity`, then the ratify/reject POSTs.
5. Notebook tab with the focus-trap fix.

Steps 1–2 are inert without 3: proposals accumulate and nothing reaches the prompt. That
is a safe place to stop and look at real candidates before wiring composition.

## Open questions

1. Bind `127.0.0.1` or add a secret header before shipping ratify/reject?
2. Should ratified entries influence recall scoring? Deferred — belongs with the
   reflection-in-recall work item.
3. Retroactive pass over the existing 8 reflections: worth a one-off tool, or start clean?
