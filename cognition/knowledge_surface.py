"""
Knowledge surface — Phase 4 Layer 2, step 4 (facts block).

Renders the reconciled relational notebook into a compact ``[WHAT YOU KNOW]``
block for the chat system prompt. Pure, offline, no NIM and no embeddings — it
reads the current view produced by knowledge_reconcile.

When *user_input* is provided, the block is targeted: only facts relevant to
the incoming message are surfaced, via simple word-overlap and entity-name
matching. When *user_input* is None, the old global top-N behavior is preserved
(for tests / backwards compatibility).

Contract: never raises into the chat path. Returns None when the gate is off,
the notebook is empty, or anything goes wrong — same discipline as the Membrane
and semantic recall.
"""

from __future__ import annotations

import re

from config.settings import KNOWLEDGE_ENABLED
from cognition.knowledge_reconcile import reconcile_notebook

# Split on any non-alphanumeric sequence, discarding empty strings.
_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")


# ── helpers ──────────────────────────────────────────────────────────────

def _word_set(text: str) -> set[str]:
    """Lowercase alphanumeric word tokens, no empty strings, no punctuation."""
    return set(_WORD_SPLIT_RE.split(text.lower())) - {""}


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


def _fact_text(rel: dict, entities: dict) -> str:
    """Render the full fact string used for lexical overlap matching."""
    subject = _entity_name(entities, rel.get("subject_id", ""))
    predicate = (rel.get("predicate") or "").replace("_", " ")
    obj = _object_text(entities, rel.get("object", {}))
    return f"{subject} {predicate} {obj}"


# ── entity mention detection ────────────────────────────────────────────

def _mentioned_entity_ids(user_input: str, entities: dict) -> set[str]:
    """Return set of entity ids whose name, alias, or id-tail appears in input.

    Only literally-named entities are included (``person:elliot`` is NOT
    auto-injected here — the ranking function separately boosts Elliot facts
    so they have a path to surface even when he isn't named).
    """
    if not user_input:
        return set()
    input_words = _word_set(user_input)
    mentioned: set[str] = set()

    for eid, ent in entities.items():
        # Check full name
        name = (ent.get("name") or "").lower().strip()
        if name and name in input_words:
            mentioned.add(eid)
            continue
        # Check each alias
        aliases = [a.lower().strip() for a in (ent.get("aliases") or []) if a]
        if any(a in input_words for a in aliases):
            mentioned.add(eid)
            continue
        # Weak fallback: entity id tail (e.g. "maya" from "person:maya")
        tail = eid.split(":")[-1] if ":" in eid else eid
        if tail and tail in input_words:
            mentioned.add(eid)

    return mentioned


# ── relevance filter and scorer ─────────────────────────────────────────

def _is_relevant(
    rel: dict,
    mentioned_ids: set[str],
    input_words: set[str],
    entities: dict,
) -> bool:
    """Return True if *rel* should be included for the current user_input.

    A fact is relevant when:
      1. Its subject or object entity was literally named in the input, OR
      2. It shares at least 2 word tokens with the input (lexical overlap).
    """
    subject_id = rel.get("subject_id", "")
    obj = rel.get("object", {})
    obj_id = obj.get("value", "") if obj.get("kind") == "entity" else ""

    # Entity literal mention: an entity name/alias was literally in the input
    if subject_id in mentioned_ids:
        return True
    if obj_id and obj_id in mentioned_ids:
        return True

    # Lexical overlap: at least 2 shared word tokens
    fw = _word_set(_fact_text(rel, entities))
    if len(input_words & fw) >= 2:
        return True

    return False


def _relevance_key(rel, mentioned_ids, input_words, entities):
    """Sort key (descending): most relevant first."""
    # Authority (same as _rank_key)
    if rel.get("locked"):
        authority = 2
    elif rel.get("origin") == "elliot":
        authority = 1
    else:
        authority = 0

    # Entity‑match boost: subject/object is a mentioned entity
    subject_id = rel.get("subject_id", "")
    obj = rel.get("object", {})
    obj_id = obj.get("value", "") if obj.get("kind") == "entity" else ""
    entity_match = 0
    if subject_id in mentioned_ids:
        entity_match += 1
    if obj_id and obj_id in mentioned_ids:
        entity_match += 1
    # Always boost facts about Elliot (even when not literally named), so
    # personal facts have a path to surface for topical/paraphrased queries.
    if subject_id == "person:elliot":
        entity_match += 1

    # Lexical overlap (all word tokens)
    fw = _word_set(_fact_text(rel, entities))
    lexical = len(input_words & fw)

    try:
        confidence = float(rel.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return (authority, entity_match, lexical, confidence, rel.get("ts") or "")


# ── public function ──────────────────────────────────────────────────────

def build_knowledge_block(
    notebook: str = "relational",
    *,
    user_input: str | None = None,
    max_facts: int = 12,
    max_chars: int = 1200,
) -> str | None:
    """Return the ``[WHAT YOU KNOW]`` block targeted to *user_input*, or None.

    Gated by KNOWLEDGE_ENABLED so it stays inert until first-light is armed.
    When *user_input* is None, falls back to the old global top-N behaviour
    (no targeting).
    """
    if not KNOWLEDGE_ENABLED:
        return None
    try:
        view = reconcile_notebook(notebook)
        entities = view.get("entities", {})
        relations = view.get("relations", {})
        if not relations:
            return None

        # ── targeted mode (with user_input) ──────────────────────────
        if user_input is not None:
            mentioned_ids = _mentioned_entity_ids(user_input, entities)
            input_words = _word_set(user_input)

            candidates = [
                rel
                for rel in relations.values()
                if _is_relevant(rel, mentioned_ids, input_words, entities)
            ]
            if not candidates:
                return None

            ranked = sorted(
                candidates,
                key=lambda r: _relevance_key(r, mentioned_ids, input_words, entities),
                reverse=True,
            )[:max_facts]

        # ── global mode (no user_input — backwards compat) ──────────
        else:
            ranked = sorted(relations.values(), key=_rank_key, reverse=True)[:max_facts]

        # ── format the block ─────────────────────────────────────────
        lines = [
            "[WHAT YOU KNOW]",
            "Durable facts about Elliot and his world that seem relevant to "
            "this turn. Treat these as current; if one is off, he'll correct it.",
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

        if len(lines) <= 3:
            return None

        block = "\n".join(lines).strip()
        if len(block) > max_chars:
            block = block[:max_chars].rstrip() + "\n...[truncated]"
        return block or None

    except Exception:
        return None


# ── Self-test ───────────────────────────────────────────────────────────
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

    # ── entities ────────────────────────────────────────────────────
    elliot = knowledge_store.make_entity_id("person", "Elliot")
    sage = knowledge_store.make_entity_id("project", "Sage")
    maya = knowledge_store.make_entity_id("person", "Maya")

    knowledge_store.append_entity("relational", id=elliot, type="person", name="Elliot", origin="she")
    knowledge_store.append_entity("relational", id=sage, type="project", name="Sage", aliases=["Sage v2"], origin="she")
    knowledge_store.append_entity("relational", id=maya, type="person", name="Maya", origin="she")

    # ── relations ───────────────────────────────────────────────────
    # Elliot grew up in low-income neighborhood
    knowledge_store.append_relation(
        "relational",
        id=knowledge_store.make_relation_id(elliot, "grew_up_in", "a low-income neighborhood"),
        subject_id=elliot, predicate="grew_up_in",
        object_value="a low-income neighborhood", object_kind="literal",
        origin="she", confidence=0.9)

    # Elliot likes ramen
    knowledge_store.append_relation(
        "relational",
        id=knowledge_store.make_relation_id(elliot, "likes", "ramen"),
        subject_id=elliot, predicate="likes",
        object_value="ramen", object_kind="literal",
        origin="she", confidence=0.5)

    # Elliot builds Sage
    knowledge_store.append_relation(
        "relational",
        id=knowledge_store.make_relation_id(elliot, "builds", sage),
        subject_id=elliot, predicate="builds",
        object_value=sage, object_kind="entity",
        origin="she", confidence=0.8)

    # Maya lives in Bandung
    knowledge_store.append_relation(
        "relational",
        id=knowledge_store.make_relation_id(maya, "lives_in", "Bandung"),
        subject_id=maya, predicate="lives_in",
        object_value="Bandung", object_kind="literal",
        origin="she", confidence=0.7)

    # ── supplementary: locked-conflict (correction wins) ─────────────
    knowledge_store.append_relation(
        "relational",
        id=knowledge_store.make_relation_id(elliot, "lives_in", "Paris"),
        subject_id=elliot, predicate="lives_in",
        object_value="Paris", object_kind="literal",
        origin="she", confidence=0.6)
    knowledge_store.append_relation(
        "relational",
        id=knowledge_store.make_relation_id(elliot, "lives_in", "Jakarta"),
        subject_id=elliot, predicate="lives_in",
        object_value="Jakarta", object_kind="literal",
        origin="elliot", locked=True, confidence=1.0)

    # ================================================================
    # 1. Gate off → None
    # ================================================================
    globals()["KNOWLEDGE_ENABLED"] = False
    assert build_knowledge_block("relational", user_input="test") is None
    globals()["KNOWLEDGE_ENABLED"] = True

    # ================================================================
    # 2. Targeted: query about childhood → grew_up_in surfaces, likes ramen does not
    # ================================================================
    block = build_knowledge_block("relational", user_input="conditions a kid grows up in and intuition")
    assert block is not None, f"expected block for childhood query, got None"
    assert "[WHAT YOU KNOW]" in block
    assert "low-income neighborhood" in block, f"grew_up_in should surface for childhood query"
    assert "ramen" not in block, f"likes ramen should NOT surface for childhood query"

    # ================================================================
    # 3. Targeted: query about Maya → Maya fact surfaces
    # ================================================================
    block = build_knowledge_block("relational", user_input="how is Maya doing")
    assert block is not None, f"expected block for Maya query"
    assert "Maya" in block, f"Maya facts should surface"
    assert "lives in" in block or "lives_in" in block, block
    # Other facts without Maya mention OR lexical overlap >=2 should not appear
    assert "ramen" not in block, f"unrelated facts should not appear for Maya query"

    # ================================================================
    # 4. Targeted: query about Sage → builds Sage surfaces
    # ================================================================
    block = build_knowledge_block("relational", user_input="tell me about Sage")
    assert block is not None, f"expected block for Sage query"
    assert "builds" in block, f"builds Sage should surface for Sage query"
    assert "ramen" not in block, f"unrelated facts should not appear for Sage query"

    # ================================================================
    # 5. No-match query → None
    # ================================================================
    block = build_knowledge_block("relational", user_input="what is the weather like today")
    assert block is None, f"no relevant facts should return None"

    # ================================================================
    # 6. Locked correction still wins (Jakarta > Paris) with targeting
    # ================================================================
    block = build_knowledge_block("relational", user_input="where does Elliot live now")
    assert block is not None
    assert "Jakarta" in block, f"locked Jakarta should beat Paris"
    assert "Paris" not in block, f"Paris should be suppressed by reconcile"
    assert "(you confirmed)" in block, f"locked fact should have confirmed marker"

    # ================================================================
    # 7. Empty notebook + targeting → None
    # ================================================================
    # Point relational to a fresh empty dir for this test
    empty_dir = Path(tempfile.mkdtemp())
    knowledge_store._NOTEBOOK_PATHS["relational"] = (
        empty_dir / "empty_e.jsonl",
        empty_dir / "empty_r.jsonl",
    )
    block = build_knowledge_block("relational", user_input="anything")
    assert block is None, f"empty notebook should return None"
    shutil.rmtree(empty_dir)

    # ================================================================
    # 8. Global mode (no user_input) — backwards compat: returns block
    # ================================================================
    # Restore the original notebook paths for the next test
    knowledge_store._NOTEBOOK_PATHS["relational"] = (
        tmp / "relational_entities.jsonl",
        tmp / "relational_relations.jsonl",
    )
    block = build_knowledge_block("relational")  # no user_input
    assert block is not None, "global mode should return block"
    assert "[WHAT YOU KNOW]" in block
    # All facts should appear in global mode (including likes ramen)
    assert "ramen" in block, "global mode should include all facts"

    shutil.rmtree(tmp)
    print("OK")
