"""
Graph API — Phase 5 Brick 4.5 + Brick 6.

Reads / view layer (Brick 4.5):
    Renders the reconciled relational notebook into a clean graph with
    predicate canonicalization, reciprocal merge, subsumption, confidence
    floor, and orphan-node pruning — all at the view layer.  The raw store
    is never mutated.

Edit / review layer (Brick 6):
    Adds a ``GET /api/graph/review`` queue of below-floor edges and three
    write endpoints (``confirm`` / ``fix`` / ``delete``) that mutate the
    append-only store solely through the defined helpers
    (``correct_relation``, ``dismiss_relation``) and re-trigger reconcile.

Graceful degradation: read paths never raise into HTTP.  Write paths
return ``{"ok": False, "error": ...}`` on failure so the client can
recover and re-fetch.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from cognition.knowledge_extraction import _PREDICATE_CATEGORIES
from cognition.knowledge_reconcile import reconcile_notebook
from memory import knowledge_store


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

# Notebook this API operates on.  Brick 6 stays relational-only.
_NOTEBOOK = "relational"


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
    edges: list[dict],
    min_confidence: float,
    drop_ledger: list[dict],
    nodes_by_id: dict[str, dict],
) -> list[dict]:
    """Rule 4: drop edges below *min_confidence* unless locked/origin=elliot.

    Each below-floor drop-ledger entry is enriched with the relation id,
    resolved subject/object display names, category, origin, and the two
    endpoint node ids so the review endpoint can return full detail without
    re-walking the reconciled view.
    """
    kept: list[dict] = []
    for e in edges:
        if e.get("locked") or e.get("origin") == "elliot":
            kept.append(e)
        elif (e.get("confidence") or 0.0) >= min_confidence:
            kept.append(e)
        else:
            src_node = nodes_by_id.get(e["source"]) or {}
            tgt_node = nodes_by_id.get(e["target"]) or {}
            drop_ledger.append({
                "id": e.get("id"),
                "source": e["source"],
                "target": e["target"],
                "subject_name": src_node.get("name") or e["source"],
                "object_name": tgt_node.get("name") or e["target"],
                "predicate": e["predicate"],
                "category": e.get("category"),
                "confidence": e.get("confidence"),
                "origin": e.get("origin"),
                "locked": e.get("locked", False),
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
    nodes: list[dict],
    edges: list[dict],
    min_confidence: float,
    nodes_by_id: dict[str, dict],
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
    edges = _apply_confidence_floor(edges, min_confidence, drop_ledger, nodes_by_id)

    # Step 4b — deduplicate identical edges (canonicalization + merge
    # can produce duplicates).
    edges = _deduplicate_edges(edges, drop_ledger)

    # Rule 5 — orphan prune
    nodes, edges, dropped_node_ids = _prune_orphans(nodes, edges)

    return nodes, edges, drop_ledger, dropped_node_ids


# ═══════════════════════════════════════════════════════════════════
# Shared builder — used by both /api/graph and /api/graph/review
# ═══════════════════════════════════════════════════════════════════


def _build_graph(min_confidence: float) -> dict:
    """Build the reconciled, hygiene-passed graph for the relational notebook.

    Returns a dict with:
        nodes, edges, drop_ledger, dropped_node_ids,
        nodes_by_id_full (pre-prune, used for review enrichment),
        fact_count, min_confidence.
    """
    view = reconcile_notebook(_NOTEBOOK)
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
    literal_drops: list[dict] = []

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

    # Pass 2 — attach literal facts (subject to the floor too); skipping any
    # relation that was grounded into an entity edge.  Above-floor literals
    # attach to their subject node before hygiene runs so orphan-prune sees
    # them; below-floor literals divert to ``literal_drops`` and are merged
    # into the drop ledger after hygiene so the review queue can surface them
    # alongside below-floor edges.
    for relation in relations.values():
        obj = relation.get("object") or {}
        obj_kind = obj.get("kind")

        if obj_kind != "literal":
            continue

        if relation.get("id") in edge_relation_ids:
            continue

        subject_id = relation.get("subject_id")
        node = nodes_by_id.get(subject_id)
        if not node:
            continue

        provenance = relation.get("provenance") or []
        category = _PREDICATE_CATEGORIES.get(relation.get("predicate"), "other")
        is_protected = relation.get("locked") or relation.get("origin") == "elliot"
        conf = relation.get("confidence") or 0.0

        if is_protected or conf >= min_confidence:
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
        else:
            # Defer ledger append until after hygiene; record what we need.
            literal_drops.append(
                {
                    "id": relation.get("id"),
                    "source": subject_id,
                    "target": obj.get("value"),
                    "subject_name": node.get("name") or subject_id,
                    "object_name": str(obj.get("value")),
                    "predicate": relation.get("predicate"),
                    "category": category,
                    "confidence": conf,
                    "origin": relation.get("origin"),
                    "locked": relation.get("locked", False),
                    "object_kind": "literal",
                    "reason": "below-floor",
                }
            )

    # Snapshot the full node lookup BEFORE orphan-prune so the review
    # endpoint can resolve names for endpoints that get pruned out.
    nodes_by_id_full = dict(nodes_by_id)

    nodes = list(nodes_by_id.values())
    nodes, edges, drop_ledger, dropped_node_ids = _run_hygiene(
        nodes, edges, min_confidence, nodes_by_id_full
    )

    # Merge below-floor literal facts into the drop ledger so the review
    # endpoint surfaces them with the same shape as below-floor edges.
    drop_ledger.extend(literal_drops)

    return {
        "nodes": nodes,
        "edges": edges,
        "drop_ledger": drop_ledger,
        "dropped_node_ids": dropped_node_ids,
        "nodes_by_id_full": nodes_by_id_full,
        "fact_count": fact_count,
        "min_confidence": min_confidence,
    }


# ═══════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════


def _base_meta(min_confidence: float) -> dict:
    return {
        "notebook": _NOTEBOOK,
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
        built = _build_graph(min_confidence)
        nodes = built["nodes"]
        edges = built["edges"]
        drop_ledger = built["drop_ledger"]
        dropped_node_ids = built["dropped_node_ids"]

        meta = _base_meta(min_confidence)
        meta.update(
            {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "fact_count": built["fact_count"],
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
                "notebook": _NOTEBOOK,
                "error": str(exc),
            },
        }


# ── /api/graph/review ────────────────────────────────────────────────


@router.get("/api/graph/review")
async def get_graph_review(
    state: str = "pending",
    category: str | None = None,
    min_confidence: float | None = None,
    max_confidence: float | None = None,
    endpoint: str | None = None,
):
    """Return the review queue: below-floor, unlocked, non-elliot, non-tombstoned edges.

    Filters (all optional):
        state          — pending | confirmed | dismissed (v1 only emits pending)
        category       — restrict to a single predicate category
        min_confidence — float, inclusive lower bound
        max_confidence — float, inclusive upper bound
        endpoint       — restrict to edges where *endpoint* is source or target

    Sorted by confidence ascending (worst first).
    """
    try:
        built = _build_graph(_DEFAULT_MIN_CONFIDENCE)
        drop_ledger = built["drop_ledger"]

        # v1 only emits pending items.  Future states (confirmed/dismissed)
        # are surfaced by re-reconciling and inspecting raw store records;
        # for now we honour the filter so clients can request them without
        # error and just return an empty list for non-pending.
        items: list[dict] = []
        if state == "pending":
            for d in drop_ledger:
                if d.get("reason") != "below-floor":
                    continue
                if d.get("locked"):
                    continue
                if d.get("origin") == "elliot":
                    continue
                items.append(
                    {
                        "id": d.get("id"),
                        "subject_id": d.get("source"),
                        "subject_name": d.get("subject_name"),
                        "predicate": d.get("predicate"),
                        "object_value": d.get("target"),
                        "object_name": d.get("object_name"),
                        "object_kind": d.get("object_kind") or "entity",
                        "category": d.get("category") or "other",
                        "confidence": d.get("confidence"),
                        "origin": d.get("origin"),
                        "source": d.get("source"),
                        "target": d.get("target"),
                        "state": "pending",
                    }
                )

        # ── Filters ──
        if category:
            items = [it for it in items if it.get("category") == category]
        if min_confidence is not None:
            items = [
                it for it in items
                if (it.get("confidence") or 0.0) >= float(min_confidence)
            ]
        if max_confidence is not None:
            items = [
                it for it in items
                if (it.get("confidence") or 0.0) <= float(max_confidence)
            ]
        if endpoint:
            items = [
                it for it in items
                if it.get("source") == endpoint or it.get("target") == endpoint
            ]

        items.sort(key=lambda it: (it.get("confidence") or 0.0))

        return {
            "items": items,
            "meta": {
                "notebook": _NOTEBOOK,
                "state": state,
                "count": len(items),
                "min_confidence_floor": _DEFAULT_MIN_CONFIDENCE,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
    except Exception as exc:
        return {
            "items": [],
            "meta": {
                "notebook": _NOTEBOOK,
                "error": str(exc),
            },
        }


# ── Write endpoints (confirm / fix / delete) ─────────────────────────


def _find_relation_by_id(relation_id: str) -> dict | None:
    """Look up the latest raw record matching *relation_id*.

    Walks the raw notebook (newest-last in append order) and returns the
    most recent record carrying that id, or ``None`` if not found.  Used
    only to recover subject/predicate/object for fix and delete when the
    client passes just the id.
    """
    for rec in reversed(knowledge_store.load_relations(_NOTEBOOK)):
        if rec.get("id") == relation_id:
            return rec
    return None


class _ConfirmBody(BaseModel):
    relation_id: str | None = None
    subject_id: str | None = None
    predicate: str | None = None
    object_value: str | None = None
    object_kind: str | None = None


class _FixBody(BaseModel):
    relation_id: str
    new_predicate: str | None = None
    new_object_value: str | None = None
    new_object_kind: str | None = None


class _DeleteBody(BaseModel):
    relation_id: str


def _ok(payload: dict) -> dict:
    return {"ok": True, **payload}


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


@router.post("/api/graph/confirm")
async def post_graph_confirm(body: _ConfirmBody):
    """Confirm an edge: re-emit it via ``correct_relation`` (locked/elliot)."""
    try:
        subj = body.subject_id
        pred = body.predicate
        obj_val = body.object_value
        obj_kind = body.object_kind

        if body.relation_id and not (subj and pred and obj_val):
            rec = _find_relation_by_id(body.relation_id)
            if not rec:
                return _err(f"relation_id {body.relation_id} not found")
            subj = subj or rec.get("subject_id")
            pred = pred or rec.get("predicate")
            o = rec.get("object") or {}
            obj_val = obj_val or o.get("value")
            obj_kind = obj_kind or o.get("kind")

        if not (subj and pred and obj_val):
            return _err("subject_id, predicate, and object_value required")
        if obj_kind not in ("entity", "literal"):
            obj_kind = "literal"

        new_id = knowledge_store.correct_relation(
            _NOTEBOOK,
            subject_id=subj,
            predicate=pred,
            object_value=obj_val,
            object_kind=obj_kind,
            confidence=1.0,
            locked=True,
        )
        if new_id is None:
            return _err("correct_relation failed")

        # Force a fresh reconcile (cheap; reconcile is pure over the store).
        reconcile_notebook(_NOTEBOOK)
        return _ok({"id": new_id, "action": "confirm"})
    except Exception as exc:
        return _err(str(exc))


@router.post("/api/graph/fix")
async def post_graph_fix(body: _FixBody):
    """Fix an edge: tombstone the old id and emit a corrected, locked edge."""
    try:
        rec = _find_relation_by_id(body.relation_id)
        if not rec:
            return _err(f"relation_id {body.relation_id} not found")

        old_obj = rec.get("object") or {}
        subj = rec.get("subject_id")
        old_pred = rec.get("predicate")
        old_val = old_obj.get("value")
        old_kind = old_obj.get("kind") or "literal"

        new_pred = body.new_predicate or old_pred
        new_val = body.new_object_value if body.new_object_value is not None else old_val
        new_kind = body.new_object_kind or old_kind
        if new_kind not in ("entity", "literal"):
            new_kind = "literal"

        if not (subj and new_pred and new_val):
            return _err("subject_id, predicate, and object_value required for fix")

        # No-op guard: if nothing actually changed, treat as confirm.
        if new_pred == old_pred and new_val == old_val and new_kind == old_kind:
            new_id = knowledge_store.correct_relation(
                _NOTEBOOK,
                subject_id=subj,
                predicate=new_pred,
                object_value=new_val,
                object_kind=new_kind,
                confidence=1.0,
                locked=True,
            )
            if new_id is None:
                return _err("correct_relation failed")
            reconcile_notebook(_NOTEBOOK)
            return _ok({"new_id": new_id, "tombstoned": None, "action": "confirm-noop"})

        # Emit the corrected edge first, then tombstone the old id.
        new_id = knowledge_store.correct_relation(
            _NOTEBOOK,
            subject_id=subj,
            predicate=new_pred,
            object_value=new_val,
            object_kind=new_kind,
            confidence=1.0,
            locked=True,
        )
        if new_id is None:
            return _err("correct_relation failed")

        tomb_id = knowledge_store.dismiss_relation(
            _NOTEBOOK,
            relation_id=body.relation_id,
            subject_id=subj,
            predicate=old_pred,
            object_value=old_val,
            object_kind=old_kind,
        )
        if tomb_id is None:
            return _err("dismiss_relation failed")

        reconcile_notebook(_NOTEBOOK)
        return _ok({"new_id": new_id, "tombstoned": tomb_id, "action": "fix"})
    except Exception as exc:
        return _err(str(exc))


@router.post("/api/graph/delete")
async def post_graph_delete(body: _DeleteBody):
    """Delete an edge: append a tombstone for its id."""
    try:
        rec = _find_relation_by_id(body.relation_id)
        if not rec:
            return _err(f"relation_id {body.relation_id} not found")
        obj = rec.get("object") or {}
        tomb_id = knowledge_store.dismiss_relation(
            _NOTEBOOK,
            relation_id=body.relation_id,
            subject_id=rec.get("subject_id"),
            predicate=rec.get("predicate"),
            object_value=obj.get("value"),
            object_kind=obj.get("kind") or "literal",
        )
        if tomb_id is None:
            return _err("dismiss_relation failed")
        reconcile_notebook(_NOTEBOOK)
        return _ok({"tombstoned": tomb_id, "action": "delete"})
    except Exception as exc:
        return _err(str(exc))
