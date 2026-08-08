# Retired fact-model inventory

Target architecture: timestamped events with `said_at`, fuzzy/null
`happened_at`, and state computed at recall. Authority:
`Sage_v2.0.1_BLUEPRINT.md` decision log, 2026-08-02 and 2026-08-03.

This is live inventory, not permission to extend it. No feature growth in
these paths. Do not delete them before event-model replacement is live.

| Live surface | Current role | Replacement / deletion trigger |
| --- | --- | --- |
| `memory/relational_api.py` facts, gaps, locks, provenance, promotions | Relational fact store and current views | Event store plus recall-time derivation; remove after MEM-001 migration. |
| `memory/knowledge_store.py`, `memory/knowledge_recall.py` | JSONL fact path / fact recall | Event log and event recall; remove after JSONL migration. |
| `cognition/claim_extraction.py`, `cognition/knowledge_extraction.py`, `cognition/knowledge_surface.py`, `cognition/consolidation.py` | Extract, surface, and consolidate fact tuples | Event extraction/recall only where needed; remove old tuple flow after replacement. |
| `cognition/knowledge_reconcile.py` | Lock precedence/current-view reconciliation | No replacement collapser. Remove with facts and locks. |
| `backend/api/graph.py`, `backend/api/desk.py`, drawer desk/graph UI | Expose facts, corrections, nominations | Event-backed people/entity index only if product decision keeps graph; remove desk entirely. |
| `tests/trust_suite.py`, `tests/graph_sqlite_test.py`, legacy module self-checks | Guard present live legacy behavior | Retire or rewrite only alongside removed surface. |

Deletion order: first event storage and recall-time projection; then held-close
recall protection on that path; then migration and read-path cutover; then
remove extract/queue/desk/lock/current-view code and matching tests together.

Why wait: current code remains load-bearing while SQLite core is off. Premature
delete breaks live behavior without implementing invariants 3–6. Restoring
locks or promotion as "safety" is prohibited: contradiction is event data.
