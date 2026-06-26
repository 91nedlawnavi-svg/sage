"""
Graph API — Phase 5 Brick 4.5: view-layer graph hygiene.

Renders the reconciled relational notebook into a clean graph with
predicate canonicalization, reciprocal merge, subsumption, confidence
floor, and orphan-node pruning — all at the view layer.  The raw store
is never mutated.

Graceful degradation: never raises into the HTTP path.  Returns empty
views on any error so the chat / heartbeat path stays alive.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from cognition.knowledge_extraction import _PREDICATE_CATEGORIES
from cognition.knowledge_reconcile import reconcile_notebook


router = APIRouter()

# ═══════════════════════════════════════════════════════════════════
# Graph hygiene — view-only
# ═══════════════════════════════════════════════════════════════════

# Predicates dropped entirely (noise).
_DROP_PREDICATES: frozenset[str] = frozenset({
    "interacts_with",
    "refers_to",
    "collaborates_with",
})

# Predicate canonicalization for entity↔entity edges.
_CANONICALIZE_PREDICATES: dict[str, str] = {
    "attended": "studied_at",
    "develops": "works_on",
}

# Symmetric (undirected) predicates — collapse A→B + B→A into one.
_SYMMETRIC_PREDICATES: frozenset[str] = frozenset({
    "friend_of", "knows", "related_to", "sibling_of", "partner_of",
})

# Generic (low-information) predicates — subsumed by specifics.
_GENERIC_PREDICATES: frozenset[str] = frozenset({
    "knows", "related_to",
})

# Default confidence floor for the graph API.
_DEFAULT_MIN_CONFIDENCE: float = 0.6


def _apply_canonicalization(
    edges: list[dict], drop_ledger: list[dict]
) -> list[dict]:
    """Rule 1: canonicalize or drop noise predicates.

    Locked / origin=elliot edges pass through every rule untouched.
    """
    kept: list[dict] = []
    for e in edges:
        if e.get("locked") or e.get("origin") == "elliot":
            kept.append(e)
            continue
        pred = e["predicate"]
        if pred in _DROP_PREDICATES:
            drop_ledger.append({
                "source": e["source"],
                "target": e["target"],
                "predicate": pred,
                "reason": "dropped-noise",
            })
            continue
        canonical = _CANONICALIZE_PREDICATES.get(pred)
        if canonical:
            drop_ledger.append({
                "source": e["source"],
                "target": e["target"],
                "predicate": pred,
                "reason": f"canonicalized->{canonical}",
            })
            e["predicate"] = canonical
        kept.append(e)
    return kept


def _merge_reciprocal(edges: list[dict], drop_ledger: list[dict]) -> list[dict]:
    """Rule 2: collapse symmetric A→B and B→A into ONE undirected edge.

    The survivor carries ``symmetric: True``.  Locked/elliot edges are
    never dropped in favour of an unlocked counterpart.
    """
    # Group by (unordered_pair, predicate) for symmetric predicates
    groups: dict[tuple, list[dict]] = {}
    for e in edges:
        pred = e["predicate"]
        src, tgt = e["source"], e["target"]
        if pred in _SYMMETRIC_PREDICATES:
            key = (min(src, tgt), max(src, tgt), pred)
        else:
            key = (src, tgt, pred)
        groups.setdefault(key, []).append(e)

    result: list[dict] = []
    for key, group in groups.items():
        canonical_pred = key[2]
        if canonical_pred in _SYMMETRIC_PREDICATES and len(group) > 1:
            has_reverse = len({e["source"] for e in group}) > 1
            if has_reverse:
                # Mark all but the best as dropped, keep the best with symmetric flag
                def _tiebreak(e: dict) -> tuple:
                    return (
                        2 if e.get("locked") or e.get("origin") == "elliot" else 0,
                        e.get("confidence", 0) or 0,
                    )
                group.sort(key=_tiebreak, reverse=True)
                best = group[0]
                for dropped in group[1:]:
                    drop_ledger.append({
                        "source": dropped["source"],
                        "target": dropped["target"],
                        "predicate": canonical_pred,
                        "reason": "reciprocal-merged-into-"
                        f"{best['source']}->{best['target']}",
                    })
                best["symmetric"] = True
                result.append(best)
            else:
                result.extend(group)
        else:
            result.extend(group)
    return result


def _apply_subsumption(edges: list[dict], drop_ledger: list[dict]) -> list[dict]:
    """Rule 3: per unordered pair, specific beats generic.

    If ANY specific edge exists between a pair, drop ALL generic edges.
    If no specific edge exists, keep exactly ONE generic (prefer ``knows``
    over ``related_to``).  Locked/elliot edges are never touched.
    """
    pair_generic: dict[tuple[str, str], list[int]] = {}
    has_specific: dict[tuple[str, str], bool] = {}

    for i, e in enumerate(edges):
        if e.get("locked") or e.get("origin") == "elliot":
            continue  # never touched by subsumption
        src, tgt = e["source"], e["target"]
        pair = (min(src, tgt), max(src, tgt))
        pred = e["predicate"]
        if pred in _GENERIC_PREDICATES:
            pair_generic.setdefault(pair, []).append(i)
        else:
            has_specific[pair] = True

    drop_indices: set[int] = set()
    for pair, indices in pair_generic.items():
        if has_specific.get(pair):
            for idx in indices:
                drop_ledger.append({
                    "source": edges[idx]["source"],
                    "target": edges[idx]["target"],
                    "predicate": edges[idx]["predicate"],
                    "reason": "subsumed-by-specific",
                })
            drop_indices.update(indices)
        elif len(indices) > 1:
            # Keep one generic, prefer knows > related_to
            indices.sort(key=lambda i: (
                0 if edges[i]["predicate"] == "knows" else 1,
                -(edges[i].get("confidence") or 0),
            ))
            for idx in indices[1:]:
                drop_ledger.append({
                    "source": edges[idx]["source"],
                    "target": edges[idx]["target"],
                    "predicate": edges[idx]["predicate"],
                    "reason": "subsumed-generic-dedup",
                })
            drop_indices.update(indices[1:])

    return [e for i, e in enumerate(edges) if i not in drop_indices]


def _apply_confidence_floor(
    edges: list[dict], min_confidence: float, drop_ledger: list[dict]
) -> list[dict]:
    """Rule 4: drop edges below *min_confidence* unless locked/origin=elliot."""
    kept: list[dict] = []
    for e in edges:
        if e.get("locked") or e.get("origin") == "elliot":
            kept.append(e)
        elif (e.get("confidence") or 0.0) >= min_confidence:
            kept.append(e)
        else:
            drop_ledger.append({
                "source": e["source"],
                "target": e["target"],
                "predicate": e["predicate"],
                "confidence": e.get("confidence"),
                "reason": "below-floor",
            })
    return kept


def _prune_orphans(
    nodes: list[dict], edges: list[dict]
) -> tuple[list[dict], list[dict], list[str]]:
    """Rule 5: drop entity nodes with 0 surviving edges AND 0 attribute facts.

    Returns ``(kept_nodes, edges, dropped_node_ids)``.
    """
    connected: set[str] = set()
    for e in edges:
        connected.add(e["source"])
        connected.add(e["target"])

    kept_nodes: list[dict] = []
    dropped_node_ids: list[str] = []
    for n in nodes:
        if n["id"] in connected:
            kept_nodes.append(n)
        elif n.get("facts"):
            kept_nodes.append(n)
        else:
            dropped_node_ids.append(n["id"])

    return kept_nodes, edges, dropped_node_ids


def _deduplicate_edges(
    edges: list[dict], drop_ledger: list[dict]
) -> list[dict]:
    """Merge identical (source, predicate, target) edge records.

    Canonicalization and reciprocal merge can produce duplicate edges
    with the same key.  Keep the best: highest precedence
    (locked/elliot > she), then highest confidence.
    """
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for e in edges:
        key = (e["source"], e["predicate"], e["target"])
        groups.setdefault(key, []).append(e)

    result: list[dict] = []
    for key, group in groups.items():
        if len(group) == 1:
            result.append(group[0])
            continue

        def _tiebreak(e: dict) -> tuple:
            return (
                2 if e.get("locked") or e.get("origin") == "elliot" else 0,
                e.get("confidence", 0) or 0,
            )
        group.sort(key=_tiebreak, reverse=True)
        best = group[0]
        for dropped in group[1:]:
            drop_ledger.append({
                "source": dropped["source"],
                "target": dropped["target"],
                "predicate": dropped["predicate"],
                "reason": "dedup-merged",
            })
        result.append(best)
    return result


def _run_hygiene(
    nodes: list[dict], edges: list[dict], min_confidence: float
) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Run the full hygiene pipeline over *edges* and *nodes*.

    Returns ``(nodes, edges, drop_ledger, dropped_node_ids)``.
    """
    drop_ledger: list[dict] = []

    # Rule 1 — canonicalization
    edges = _apply_canonicalization(edges, drop_ledger)

    # Rule 2 — reciprocal merge
    edges = _merge_reciprocal(edges, drop_ledger)

    # Rule 3 — subsumption
    edges = _apply_subsumption(edges, drop_ledger)

    # Rule 4 — confidence floor
    edges = _apply_confidence_floor(edges, min_confidence, drop_ledger)

    # Step 4b — deduplicate identical edges (canonicalization + merge
    # can produce duplicates).
    edges = _deduplicate_edges(edges, drop_ledger)

    # Rule 5 — orphan prune
    nodes, edges, dropped_node_ids = _prune_orphans(nodes, edges)

    return nodes, edges, drop_ledger, dropped_node_ids


# ═══════════════════════════════════════════════════════════════════
# Endpoint
# ═══════════════════════════════════════════════════════════════════


def _base_meta(min_confidence: float) -> dict:
    return {
        "notebook": "relational",
        "node_count": 0,
        "edge_count": 0,
        "fact_count": 0,
        "dropped_edges": 0,
        "min_confidence": min_confidence,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/graph")
async def get_graph(min_confidence: float = _DEFAULT_MIN_CONFIDENCE):
    try:
        view = reconcile_notebook("relational")
        entities = view.get("entities", {})
        relations = view.get("relations", {})
        node_ids = set(entities.keys())

        nodes_by_id: dict[str, dict] = {}
        for entity_id, entity in entities.items():
            nodes_by_id[entity_id] = {
                "id": entity_id,
                "type": entity.get("type"),
                "name": entity.get("name"),
                "aliases": entity.get("aliases", []),
                "origin": entity.get("origin"),
                "locked": entity.get("locked", False),
                "facts": [],
            }

        edges: list[dict] = []
        fact_count = 0
        edge_relation_ids: set[str] = set()

        # Pass 1 — build entity↔entity edges; track which relation ids are used.
        for relation in relations.values():
            obj = relation.get("object") or {}
            obj_kind = obj.get("kind")

            if obj_kind != "entity":
                continue

            source = relation.get("subject_id")
            target = obj.get("value")
            if source not in node_ids or target not in node_ids:
                continue

            rid = relation.get("id")
            if rid:
                edge_relation_ids.add(rid)

            provenance = relation.get("provenance") or []
            category = _PREDICATE_CATEGORIES.get(relation.get("predicate"), "other")
            edges.append(
                {
                    "id": rid,
                    "source": source,
                    "target": target,
                    "predicate": relation.get("predicate"),
                    "category": category,
                    "confidence": relation.get("confidence"),
                    "origin": relation.get("origin"),
                    "locked": relation.get("locked", False),
                    "provenance_count": len(provenance),
                }
            )

        # Pass 2 — attach literal facts, skipping any relation that was
        # grounded into an entity edge (so it lives only as the edge, never
        # also as a raw literal property on the node).
        for relation in relations.values():
            obj = relation.get("object") or {}
            obj_kind = obj.get("kind")

            if obj_kind != "literal":
                continue

            # Skip if this relation was already emitted as a entity↔entity
            # edge (it was grounded; only the edge should represent it).
            if relation.get("id") in edge_relation_ids:
                continue

            subject_id = relation.get("subject_id")
            node = nodes_by_id.get(subject_id)
            if not node:
                continue

            provenance = relation.get("provenance") or []
            category = _PREDICATE_CATEGORIES.get(relation.get("predicate"), "other")
            node["facts"].append(
                    {
                        "predicate": relation.get("predicate"),
                        "value": obj.get("value"),
                        "category": category,
                        "confidence": relation.get("confidence"),
                        "origin": relation.get("origin"),
                        "locked": relation.get("locked", False),
                        "provenance_count": len(provenance),
                        "relation_id": relation.get("id"),
                    }
            )
            fact_count += 1

        # ── Hygiene pipeline ─────────────────────────────────────
        nodes = list(nodes_by_id.values())
        nodes, edges, drop_ledger, dropped_node_ids = _run_hygiene(
            nodes, edges, min_confidence
        )

        meta = _base_meta(min_confidence)
        meta.update(
            {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "fact_count": fact_count,
                "dropped_edges": len(drop_ledger),
                "dropped_nodes": dropped_node_ids,
            }
        )
        return {"nodes": nodes, "edges": edges, "meta": meta}
    except Exception as exc:
        return {
            "nodes": [],
            "edges": [],
            "meta": {
                "notebook": "relational",
                "error": str(exc),
            },
        }
