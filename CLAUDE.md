# CLAUDE.md — Temporary Opus 4.8 Review Mode for Sage

> **Temporary session instructions for ClaudeCode running Opus 4.8.**
> This file supersedes the older “hands-only executor” posture for this short review window.
>
> You are **not** merely a mechanical patch applier in this session. You are acting as a **surgical architecture reviewer** for Codex’s uncommitted changes.

---

## Role for this session

You are reviewing a large Codex patch on top of `phase4.2`.

Your job is to decide what is safe to keep, what must be reverted, and whether the Task D semantic fact-selection feature actually delivers its intended behavior.

Use judgment. Do not blindly preserve Codex’s changes.

---

## Hard constraints

1. **No commit.**
   Leave all changes uncommitted until the brain explicitly signs off.

2. **No broad rewrite.**
   Token window is short. Review first. Patch only if the fix is small and clearly necessary.

3. **Do not act as a hands-only executor.**
   You may say “this design is wrong,” “revert this,” or “keep this.”

4. **Preserve chat-path latency.**
   Task D must not add a second embedding call on the chat turn.

5. **Do not lower semantic threshold just to show a win.**
   Trap cleanliness matters more than making the feature look successful.

6. **HOLD after review.**
   Report findings and wait. Do not merge, tag, deploy, or push.

---

## Current known baseline

Signed-off baseline:

- Branch/master at `d44e363`
- Tag: `phase4.2`
- Phase 4 L2 signed off before Codex patch
- Knowledge layer gated/live via `SAGE_KNOWLEDGE_ENABLED=1`

Codex has made uncommitted changes across many files, including:

```text
backend/api/chat.py
backend/app.py
backend/heartbeat.py
backend/session.py
cognition/knowledge_builder.py
cognition/knowledge_extraction.py
cognition/knowledge_reconcile.py
cognition/knowledge_surface.py
config/settings.py
memory/semantic_recall.py
memory/knowledge_recall.py
models/prompts/templates.py
tests/l2_felt_test.py
README.md
CLAUDE.md
frontend/index.html
launch.py
```

---

## What the brain already believes is probably correct

### Task A — builder overlap bound

Likely correct:

- `knowledge_builder.py` checks `session.chat_active()` after cursor persist.
- `run()` checks again before moving to next notebook.
- Builder heartbeat timeout lowered `45s -> 25s` only for builder.

### Task B — predicate normalization

Likely correct:

- Controlled predicate aliases and patterns.
- `had_unpleasant_experiences_due_to -> affected_by`.
- Long freeform predicates map to `related_to`.

### Task C — deterministic dedup

Likely correct:

- View-only normalization.
- Normalized literal values used only for grouping/id computation.
- Survivor keeps original display casing.
- No semantic/embedding dedup.

### Task D wiring

Likely correct architecturally:

- `memory/knowledge_recall.py` is an off-chat-path fact embedding cache.
- `chat.py` computes query embedding once.
- Same query vector is passed to both semantic recall and knowledge fact selection.
- `knowledge_surface.py` does not call e5 directly.

But Task D may be dormant because the default threshold remains `0.80`.

---

## Main review risks

### Risk 1 — `knowledge_surface.py` rewrite

Codex changed signed-off first-person surfacing behavior.

Old behavior:

- `i/my/mine/myself` injects `person:elliot`.
- Elliot facts could surface broadly.
- Ranking/model judgment handled usefulness.

Codex behavior:

- Uses hand-written predicate/detail aliases.
- Narrows first-person queries.
- Example: `who was I close with` now surfaces relationship facts and excludes unrelated `grew_up_in`.

This may be cleaner, but it may create false negatives:

```text
how did my upbringing shape me
how did my background affect who I became
what do you know about my childhood
what do you remember about me
```

Review whether to keep, revert, or replace with a small hybrid.

Preferred if uncertain:

- Broad personal queries should surface broadly.
- Specific personal queries may narrow only when confidence is high.
- Object-pronoun imperatives like `tell me about Sage` must not inject Elliot facts.

### Risk 2 — Task D threshold

Default:

```python
SAGE_KNOWLEDGE_FACT_MIN_SIM = 0.80
```

Earlier live observations:

- Relevant neutral queries around `0.76–0.77`
- Trap queries around `0.64–0.67`

So at `0.80`, semantic selection is safe but probably inactive.

Do not lower blindly. Calibrate if possible.

### Risk 3 — tests are mock-vector proof only

Offline tests prove wiring, not real e5 separation.

Do not treat mock-vector felt-test pass as proof the feature delivers live.

### Risk 4 — out-of-scope changes

Review quickly:

- `backend/app.py`
- `backend/session.py`
- `launch.py`
- `frontend/index.html`
- docs/status wording
- `tools/rebuild_relational.py`

Classify keep/revert.

---

## Priority order for short Opus window

1. Review `cognition/knowledge_surface.py` behavior.
2. Review/calibrate Task D semantic threshold.
3. Confirm one and only one chat-path embed.
4. Classify out-of-scope changes.
5. Run only high-value tests.

---

## Required output format

Return this exact structure:

```text
VERDICT: accept / accept-with-small-fixes / reject-and-revert

1. knowledge_surface decision:
   - keep/revert/hybrid
   - why
   - patch made, if any

2. Task D semantic threshold:
   - calibrated? yes/no
   - numbers if available
   - recommend threshold or keep 0.80 conservative

3. chat-path embed count:
   - one embed confirmed? yes/no
   - evidence

4. out-of-scope changes:
   - keep list
   - revert list

5. tests run:
   - commands + pass/fail

HOLD: no commit until brain sign-off.
```

---

## Final instruction

Be decisive. If Codex’s patch is overfit, say so. If a change is safe-but-dormant, say so. If the right answer is to keep the architecture but revert one risky behavior change, recommend that.

Do not commit.
