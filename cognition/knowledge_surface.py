"""
Knowledge surface — Phase 4 Layer 2, step 4 (facts block).

Renders the reconciled relational notebook into a compact ``[WHAT YOU KNOW]``
block for the chat system prompt. Pure, offline, no NIM and no embeddings — it
reads the current view produced by knowledge_reconcile.

Contract: never raises into the chat path. Returns None when the gate is off,
the notebook is empty, or anything goes wrong — same discipline as the Membrane
and semantic recall.

Ranking (most authoritative / useful first):
    locked/elliot-confirmed  >  higher confidence  >  more recent
"""

from __future__ import annotations

from config.settings import KNOWLEDGE_ENABLED
from cognition.knowledge_reconcile import reconcile_notebook


def _rank_key(rel: dict) -> tuple:
    """Sort key (descending): confirmed first, then confidence, then recency."""
    if rel.get("locked"):
        authority = 2
    elif rel.get("origin") == "elliot":
        authority = 1
    else:
        authority = 0
    try:
        confidence = float(rel.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return (authority, confidence, rel.get("ts") or "")


def _entity_name(entities: dict, entity_id: str) -> str:
    """Resolve an entity id to its display name; fall back to the id."""
    ent = entities.get(entity_id)
    if ent and ent.get("name"):
        return str(ent["name"]).strip()
    return entity_id


def _object_text(entities: dict, obj: dict) -> str:
    """Render a relation object: resolve entity refs, pass literals through."""
    value = (obj or {}).get("value", "")
    if (obj or {}).get("kind") == "entity":
        return _entity_name(entities, value)
    return str(value).strip()


def build_knowledge_block(
    notebook: str = "relational",
    *,
    max_facts: int = 12,
    max_chars: int = 1200,
) -> str | None:
    """Return the ``[WHAT YOU KNOW]`` block for *notebook*, or None.

    Gated by KNOWLEDGE_ENABLED so it stays inert until first-light is armed.
    """
    if not KNOWLEDGE_ENABLED:
        return None
    try:
        view = reconcile_notebook(notebook)
        entities = view.get("entities", {})
        relations = view.get("relations", {})
        if not relations:
            return None

        ranked = sorted(relations.values(), key=_rank_key, reverse=True)[:max_facts]

        lines = [
            "[WHAT YOU KNOW]",
            "Durable facts about Elliot and his world, built up and corrected over "
            "time. Treat these as current; if one is off, he'll correct it.",
            "",
        ]
        for rel in ranked:
            subject = _entity_name(entities, rel.get("subject_id", ""))
            predicate = (rel.get("predicate") or "").replace("_", " ").strip()
            obj = _object_text(entities, rel.get("object", {}))
            if not subject or not predicate or not obj:
                continue
            confirmed = " (you confirmed)" if (rel.get("locked") or rel.get("origin") == "elliot") else ""
            lines.append(f"  - {subject} {predicate} {obj}{confirmed}")

        # If every fact was malformed, nothing useful to surface.
        if len(lines) <= 3:
            return None

        block = "\n".join(lines).strip()
        if len(block) > max_chars:
            block = block[:max_chars].rstrip() + "\n...[truncated]"
        return block or None
    except Exception:
        return None


# ── Self-test ───────────────────────────────────────────
if __name__ == "__main__":
    import shutil
    import tempfile
    from pathlib import Path
    from memory import knowledge_store

    # Force the gate on for the test regardless of environment.
    globals()["KNOWLEDGE_ENABLED"] = True

    tmp = Path(tempfile.mkdtemp())
    knowledge_store._NOTEBOOK_PATHS["relational"] = (
        tmp / "relational_entities.jsonl",
        tmp / "relational_relations.jsonl",
    )

    # Entities (for name resolution)
    elliot = knowledge_store.make_entity_id("person", "Elliot")
    sage = knowledge_store.make_entity_id("project", "Sage")
    knowledge_store.append_entity("relational", id=elliot, type="person", name="Elliot", origin="she")
    knowledge_store.append_entity("relational", id=sage, type="project", name="Sage", origin="she")

    # lives_in is single-valued: machine Paris vs your locked Jakarta
    knowledge_store.append_relation(
        "relational", id=knowledge_store.make_relation_id(elliot, "lives_in", "Paris"),
        subject_id=elliot, predicate="lives_in", object_value="Paris",
        object_kind="literal", origin="she", confidence=0.6)
    knowledge_store.append_relation(
        "relational", id=knowledge_store.make_relation_id(elliot, "lives_in", "Jakarta"),
        subject_id=elliot, predicate="lives_in", object_value="Jakarta",
        object_kind="literal", origin="elliot", locked=True, confidence=1.0)

    # entity-valued relation (object id should resolve to a name)
    knowledge_store.append_relation(
        "relational", id=knowledge_store.make_relation_id(elliot, "builds", sage),
        subject_id=elliot, predicate="builds", object_value=sage,
        object_kind="entity", origin="she", confidence=0.8)

    # multi-valued 'likes' — both should survive
    knowledge_store.append_relation(
        "relational", id=knowledge_store.make_relation_id(elliot, "likes", "pizza"),
        subject_id=elliot, predicate="likes", object_value="pizza",
        object_kind="literal", origin="she", confidence=0.5)
    knowledge_store.append_relation(
        "relational", id=knowledge_store.make_relation_id(elliot, "likes", "ramen"),
        subject_id=elliot, predicate="likes", object_value="ramen",
        object_kind="literal", origin="she", confidence=0.5)

    block = build_knowledge_block("relational")
    assert block is not None, "expected a block"
    assert "[WHAT YOU KNOW]" in block
    # correction: Jakarta surfaces, Paris suppressed
    assert "Jakarta" in block, block
    assert "Paris" not in block, block
    # confirmed marker present on the locked fact
    assert "(you confirmed)" in block
    # entity-object resolved to a name, not the raw id
    assert "builds Sage" in block, block
    assert sage not in block, block
    # multi-valued kept
    assert "pizza" in block and "ramen" in block, block
    # ranking: the confirmed Jakarta fact appears before the machine 'likes' facts
    assert block.index("Jakarta") < block.index("pizza"), block

    # gate off → None
    globals()["KNOWLEDGE_ENABLED"] = False
    assert build_knowledge_block("relational") is None

    shutil.rmtree(tmp)
    print("OK")
