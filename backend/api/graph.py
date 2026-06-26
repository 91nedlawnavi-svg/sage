from datetime import datetime, timezone

from fastapi import APIRouter

from cognition.knowledge_extraction import _PREDICATE_CATEGORIES
from cognition.knowledge_reconcile import reconcile_notebook


router = APIRouter()


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
async def get_graph(min_confidence: float = 0.0):
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
        dropped_edges = 0

        for relation in relations.values():
            obj = relation.get("object") or {}
            obj_kind = obj.get("kind")
            provenance = relation.get("provenance") or []
            category = _PREDICATE_CATEGORIES.get(relation.get("predicate"), "other")

            if obj_kind == "literal":
                subject_id = relation.get("subject_id")
                node = nodes_by_id.get(subject_id)
                if not node:
                    continue
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
                continue

            if obj_kind != "entity":
                continue

            source = relation.get("subject_id")
            target = obj.get("value")
            if source not in node_ids or target not in node_ids:
                dropped_edges += 1
                continue

            if (
                relation.get("confidence", 0.0) < min_confidence
                and not relation.get("locked", False)
                and relation.get("origin") != "elliot"
            ):
                continue

            edges.append(
                {
                    "id": relation.get("id"),
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

        nodes = list(nodes_by_id.values())
        meta = _base_meta(min_confidence)
        meta.update(
            {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "fact_count": fact_count,
                "dropped_edges": dropped_edges,
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
