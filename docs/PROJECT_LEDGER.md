# Project ledger

Operational state. Architecture authority remains `Sage_v2.0.1_BLUEPRINT.md`.
This ledger records only active work, retirements, and release evidence.

## Operating rules

- One behavior per commit. Pre-existing edits go in a separate commit, or a
  clearly labeled snapshot with every affected check recorded.
- `committed`, `tested locally`, and `live` are separate states. Never infer a
  later state from an earlier one.
- A live record names fresh service activation/PID evidence and rollback
  commit or tag. `/sage-verify-deploy` is read-only verification, not deploy.
- Before a major merge/deploy, follow `docs/REVIEW_PROTOCOL.md` and record
  findings (or `none`) in release evidence.

## Backlog

| ID | Status | Outcome / acceptance checks | Dependencies | Decision |
| --- | --- | --- | --- | --- |
| MEM-001 | planned | Event store records timestamped events with exact `said_at`, fuzzy/null `happened_at`, and recall-computed state; no fact current-view replacement. | Migration design; held-close recall guard. | Blueprint 2026-08-02/03 |
| MEM-002 | planned | Remove legacy facts, locks, promotion desk, reconcile, and current-view paths only after MEM-001 serves equivalent live behavior. | MEM-001. | Blueprint 2026-08-02 |
| GRAPH-001 | product decision pending | Decide whether graph indexes only people; if accepted, graph nodes/edges expose no organizations, places, projects, topics, or Sage node. | Elliot decision; MEM-001 design. | Blueprint 2026-08-02 |
| VOICE-001 | felt test owed | Real call confirms STT, TTS, interruption, and voice feel; endpoint probes alone do not close item. | Elliot. | Blueprint 2026-08-01 |
| OPS-001 | watch | Run SQLite cutover ritual only with explicit approval; verify fresh migration, service activation, recall, and rollback point. | Elliot approval; backup. | Blueprint 2026-08-01 |

## Deprecations

| Retired surface | Live area | Event-model replacement | Removal trigger | Invariant rationale |
| --- | --- | --- | --- | --- |
| Facts/current views, locks, promotion queue, desk | `memory/`, `cognition/`, graph/desk APIs, drawer | Event recall computes state from timestamped events. | MEM-001 live and verified. | Events not frozen facts; no promotion gate. |
| `knowledge_reconcile` precedence | deterministic gate and heartbeat legacy path | None; recall computes state. | Delete with old fact model. | No replacement current-view collapser. |

See `docs/RETIRED_FACT_MODEL.md` for full delete-next inventory.

## Releases

### Unreleased

| Scope | Commit | Tested locally | Fresh-context review | Live evidence / rollback |
| --- | --- | --- | --- | --- |
| Workflow reproducibility: canonical gate, ledger, retired inventory, sealed routing policy, review protocol | not committed | `OK sage-tests` (including `--basin`) | none; protocol applied to focused diff | not deployed; rollback not applicable |

### Recorded evidence

| Scope | Commit | Tested locally | Live |
| --- | --- | --- | --- |
| Single-alias routing contract | `ce508c6` | provider-free engine checks passed when committed | no deploy recorded |
| Held-close recall firewall | `0e7ee8c` | `OK held_close_firewall_test` recorded before commit | no deploy recorded |
