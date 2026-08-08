# Fresh-context review protocol

Run before architectural changes, memory-schema migrations, privacy/routing
changes, and every release. For smaller changes, run when scope or behavior is
uncertain.

1. Run `scripts/sage-tests <changed paths>`; add `--basin` when replay matters.
   Record real terminal `OK`/`FAIL` lines.
2. Inspect focused diff and changed callers. Verify no unrelated pre-existing
   edits were silently included.
3. Apply adversarial cases at affected boundaries: failure paths, empty input,
   stale/missing authority, privacy denial, retry/fallback behavior, and
   persistence/hydration where applicable.
4. Compare affected callers with `docs/RETIRED_FACT_MODEL.md`. Do not add
   feature growth to retired fact, lock, promotion, or current-view paths.
5. Review from a fresh context when possible. If unavailable, deterministic
   evidence is primary: re-read requirements, diff, callers, and tests as a
   separate pass; fix confirmed defects only.
6. Record verified findings or `none`, canonical gate result, commit, and
   local/live state in `docs/PROJECT_LEDGER.md`.

Brick 3b is manual live quality evidence. It is never proof for this protocol's
deterministic gate and never runs without explicit selection.
