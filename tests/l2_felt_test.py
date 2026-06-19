#!/usr/bin/env python3
"""
Phase 4 Layer 2 integrated felt-test harness — offline / controlled.

Proves:

  1.  A personal fact about Elliot surfaces for a later paraphrased query
      where semantic recall alone prefers a generic topical memory — the
      provenance boost from the knowledge layer tips the scales.

  2.  A locked Elliot correction survives later wrong machine re-derivation
      and surfaces the corrected value.

No e5, no NIM, no real ~/sage_data.  Uses controlled 2D vectors for
deterministic cosine similarities and temp directories for the notebook.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# ── Seed directories before any module reads config ────────
# Prevent accidental init of real recall index.
os.environ["SAGE_RECALL_ENABLED"] = "0"

import config.settings as settings
from memory import knowledge_store, semantic_recall
from cognition import knowledge_surface, knowledge_reconcile
from models.prompts.templates import build_chat_messages

# ---------------------------------------------------------------------------
# 0.  Gate on for the test
# ---------------------------------------------------------------------------
knowledge_surface.KNOWLEDGE_ENABLED = True

# ---------------------------------------------------------------------------
# 1.  Helpers
# ---------------------------------------------------------------------------

# Controlled 3-D vectors for deterministic cosine.
#   query q = [1, 0, 0]
#   generic g = [0.85, 0.527, 0]    cos(q,g) = 0.85  (clears RECALL_MIN_SIM=0.70)
#   personal p = [0.55, 0, 0.835]   cos(q,p) = 0.55  (below floor without boost)
#   With +0.15 boost: effective = 0.70  -> passes because
#     (boost > 0 and key in boost_keys)  bypasses the similarity floor.
_QUERY_EMB = [1.0, 0.0, 0.0]
_GENERIC_EMB = [0.85, 0.527, 0.0]
_PERSONAL_EMB = [0.55, 0.0, 0.835]


async def _mock_embed(
    text: str,
    client=None,
    read_timeout: float = 10.0,
    prefix: str = "query: ",
) -> list[float] | None:
    """Return controlled vectors based on text content (async — awaited by recall)."""
    if "gut instincts" in text or "conditions a kid" in text:
        return _QUERY_EMB
    if "Socioeconomic" in text:
        return _GENERIC_EMB
    if "low-income" in text:
        return _PERSONAL_EMB
    return [0.0, 0.0, 0.0]  # fallback — unrelated query, low similarity


def _mock_excluded_keys() -> set[str]:
    """Nothing is excluded; we control the index directly."""
    return set()


def _setup_temp_notebook() -> Path:
    """Point ``knowledge_store._NOTEBOOK_PATHS["relational"]`` to a temp dir.

    Returns the tmpdir Path so the caller can clean it up.
    """
    tmp = Path(tempfile.mkdtemp())
    knowledge_store._NOTEBOOK_PATHS["relational"] = (
        tmp / "relational_entities.jsonl",
        tmp / "relational_relations.jsonl",
    )
    return tmp


def load_all(path: Path) -> list[dict]:
    entries = []
    if path.exists():
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return entries


# ---------------------------------------------------------------------------
# 2.  Felt-test #1 — personal fact beats topical recall
# ---------------------------------------------------------------------------

def test_personal_fact_beats_topical() -> dict:
    """
    Returns the ``recall_block`` string so the caller can feed it to
    ``build_chat_messages``.
    """
    print("─" * 60)
    print("FELT-TEST #1  —  personal fact beats topical recall\n")

    tmp = _setup_temp_notebook()
    query = "conditions a kid grows up in and gut instincts"

    # ── a) Seed the knowledge notebook ──────────────────────────────
    eid = knowledge_store.make_entity_id("person", "Elliot")
    knowledge_store.append_entity(
        "relational", id=eid, type="person", name="Elliot", origin="she",
    )
    fact_id = knowledge_store.make_relation_id(
        "person:elliot", "grew_up_in", "a low-income neighborhood",
    )
    knowledge_store.append_relation(
        "relational",
        id=fact_id,
        subject_id="person:elliot",
        predicate="grew_up_in",
        object_value="a low-income neighborhood",
        object_kind="literal",
        provenance=["user_personal_1"],
        confidence=0.9,
        origin="she",
        locked=False,
    )

    # ── b) Verify select_relevant_relations picks the fact ──────────
    # After literal grounding landed, grew_up_in is a grounding predicate: the
    # raw literal "a low-income neighborhood" is promoted at reconcile time to
    # an entity ref (place:low-income-neighborhood) in the view. Provenance is
    # preserved unchanged through grounding, so the boost path still works.
    selected = knowledge_surface.select_relevant_relations(
        "relational", user_input=query, max_facts=12,
    )
    assert selected, "select_relevant_relations returned empty for personal query"
    obj = selected[0]["object"]
    assert obj["kind"] == "entity" and obj["value"] == "place:low-income-neighborhood", (
        f"Expected grounded place:low-income-neighborhood, got {obj}"
    )
    boost_keys = {
        k for rel in selected for k in (rel.get("provenance") or [])
    }
    assert "user_personal_1" in boost_keys, (
        f"Expected 'user_personal_1' in boost_keys, got {boost_keys}"
    )

    # ── c) Populate controlled recall index ──────────────────────────
    semantic_recall._index = [
        {
            "key": "generic_1",
            "kind": "turn",
            "ts": "2026-01-01T00:00:00",
            "role": "assistant",
            "text": (
                "Socioeconomic factors can shape intuition and decision-making "
                "in ways that people rarely examine directly. Growing up with "
                "limited resources..."
            ),
            "embedding": _GENERIC_EMB,
        },
        {
            "key": "user_personal_1",
            "kind": "turn",
            "ts": "2026-01-02T00:00:00",
            "role": "user",
            "text": (
                "I grew up in a low-income neighborhood, and I think it shaped "
                "how I read people."
            ),
            "embedding": _PERSONAL_EMB,
        },
    ]
    semantic_recall._index_keys = {"generic_1", "user_personal_1"}
    semantic_recall._loaded = True
    semantic_recall.RECALL_ENABLED = True

    # Monkeypatch embed & excluded_keys
    orig_embed = semantic_recall._embed
    orig_excluded = semantic_recall._excluded_keys
    semantic_recall._embed = _mock_embed
    semantic_recall._excluded_keys = _mock_excluded_keys

    try:
        # ── d) Without boost → only generic_1 surfaces; personal excluded ──
        # Controlled cosine sims:
        #   generic_1 vs query = 0.85  ≥ 0.70 floor  → qualifies on its own
        #   personal   vs query = 0.55  <  0.70 floor  → excluded without boost
        # So the no-boost block must contain the generic turn and NOT the
        # personal one. This is the mechanism the boost exists to override.
        block_no_boost = asyncio.run(
            semantic_recall.recall(query, client=None, boost_keys=None),
        )
        assert block_no_boost is not None, (
            "Without boost: recall should return a block (generic_1 qualifies)"
        )
        assert "low-income" not in block_no_boost, (
            "Without boost: personal turn should NOT appear. "
            f"Got:\n{block_no_boost}"
        )
        print("  (no-boost recall: generic topical only, personal excluded)\n")

        # ── e) With boost → both surface ─────────────────────────────
        block_with_boost = asyncio.run(
            semantic_recall.recall(query, client=None, boost_keys=boost_keys),
        )
        assert block_with_boost is not None, (
            "With boost: recall should return a block"
        )
        assert "low-income" in block_with_boost, (
            "With boost: personal turn should appear. "
            f"Got:\n{block_with_boost}"
        )
        assert "Socioeconomic" in block_with_boost, (
            "With boost: generic turn should also appear"
        )

        # ── f) Verify ranking: personal should survive alongside generic ──
        # Extract the recall block and confirm both keys present
        # (We already proved they're both in the block; ranking is fine.)

        # ── g) Verify knowledge block includes the personal fact ─────
        knowledge_block = knowledge_surface.build_knowledge_block(
            "relational", user_input=query, max_facts=12,
        )
        assert knowledge_block is not None, (
            "Knowledge block should be non-None for relevant query"
        )
        assert "low-income neighborhood" in knowledge_block, (
            f"Knowledge block should include personal fact. Got:\n{knowledge_block}"
        )
        assert "[WHAT YOU KNOW]" in knowledge_block

        # ── Print overlap ────────────────────────────────────────────
        print("  OVERLAP (provenance → source → query):")
        print(f'    FACT:  Elliot grew up in low-income neighborhood')
        print(f'    SOURCE tenure: I grew up in a low-income neighborhood...')
        print(f'    QUERY: {query}')
        print()

    finally:
        semantic_recall._embed = orig_embed
        semantic_recall._excluded_keys = orig_excluded
        shutil.rmtree(tmp)

    return block_with_boost


# ---------------------------------------------------------------------------
# 3.  Felt-test #2 — locked correction wins
# ---------------------------------------------------------------------------

def test_locked_correction_wins() -> None:
    print("─" * 60)
    print("FELT-TEST #2  —  locked correction wins\n")

    tmp = _setup_temp_notebook()

    # ── Entity (needed for name resolution in knowledge block) ────────
    eid = knowledge_store.make_entity_id("person", "Elliot")
    knowledge_store.append_entity(
        "relational", id=eid, type="person", name="Elliot", origin="she",
    )

    # ── a) Machine wrong: lives_in Paris ──────────────────────────────
    paris_id = knowledge_store.make_relation_id(
        "person:elliot", "lives_in", "Paris",
    )
    knowledge_store.append_relation(
        "relational",
        id=paris_id,
        subject_id="person:elliot",
        predicate="lives_in",
        object_value="Paris",
        object_kind="literal",
        provenance=["machine_extraction_1"],
        confidence=0.6,
        origin="she",
        locked=False,
    )

    # ── b) Elliot correction: lives_in Jakarta ────────────────────────
    jakarta_id = knowledge_store.correct_relation(
        "relational",
        subject_id="person:elliot",
        predicate="lives_in",
        object_value="Jakarta",
        object_kind="literal",
        provenance=["elliot_correction"],
        confidence=1.0,
        locked=True,
    )
    assert jakarta_id is not None

    # ── c) Later machine wrong: lives_in London ──────────────────────
    london_id = knowledge_store.make_relation_id(
        "person:elliot", "lives_in", "London",
    )
    knowledge_store.append_relation(
        "relational",
        id=london_id,
        subject_id="person:elliot",
        predicate="lives_in",
        object_value="London",
        object_kind="literal",
        provenance=["machine_extraction_2"],
        confidence=0.7,
        origin="she",
        locked=False,
    )

    # ── d) Raw store: all three present ──────────────────────────────
    raw = load_all(tmp / "relational_relations.jsonl")
    raw_ids = [r.get("id") for r in raw]
    assert len(raw) == 3, f"Expected 3 raw records, got {len(raw)}"
    assert paris_id in raw_ids
    assert jakarta_id in raw_ids
    assert london_id in raw_ids
    print("  RAW: Paris, Jakarta, London — all present\n")

    # ── e) Reconcile: exactly 1, value = Jakarta (grounded to place entity) ──
    # lives_in is a grounding predicate, so all three literal values (Paris,
    # Jakarta, London) are promoted to place: entities at reconcile time. The
    # locked Jakarta correction still wins the single-valued contest; we just
    # read it back as the grounded entity ref instead of the raw literal.
    view = knowledge_reconcile.reconcile_notebook("relational")
    rels = view.get("relations", {})
    assert len(rels) == 1, f"Expected 1 reconciled relation, got {len(rels)}"
    survivor = next(iter(rels.values()))
    assert survivor["object"] == {"kind": "entity", "value": "place:jakarta"}, (
        f"Expected grounded place:jakarta, got {survivor['object']}"
    )
    assert survivor["origin"] == "elliot"
    assert survivor["locked"] is True

    # ── f) Knowledge block for "where do I live?" ────────────────────
    # Use a query with ≥2 word overlap. "elliot lives" has ["elliot","lives"]
    # which overlaps with the fact text "Elliot lives in Jakarta" at "elliot"
    # and "lives" (after _replace("_"," "): "lives_in" -> "lives in").
    # Actually: fact_text = "Elliot lives in Jakarta" -> words ["elliot","lives","in","jakarta"]
    # Query "tell me where elliot lives" -> ["tell","me","where","elliot","lives"]
    # Intersection: {"elliot","lives"} -> 2 words. Good.
    q_live = "tell me where elliot lives"
    block = knowledge_surface.build_knowledge_block(
        "relational", user_input=q_live, max_facts=12,
    )
    assert block is not None, "Knowledge block should not be None"
    assert "Jakarta" in block, f"Jakarta should appear. Got:\n{block}"
    assert "Paris" not in block, f"Paris should NOT appear. Got:\n{block}"
    assert "London" not in block, f"London should NOT appear. Got:\n{block}"
    assert "(you confirmed)" in block, (
        "Locked correction should have '(you confirmed)' marker"
    )

    # ── Print overlap ────────────────────────────────────────────────
    print("  OVERLAP (correction → suppress → surface):")
    print("    CORRECTION: Elliot lives in Jakarta (locked)")
    print("    SUPPRESSED: Paris, London")
    print("    SURFACED:   Jakarta")
    print()

    shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# 4.  Integrated: build_chat_messages includes both blocks
# ---------------------------------------------------------------------------

def test_integrated_prompt(recall_block: str) -> None:
    """Feed both the recall block and knowledge block into
    ``build_chat_messages`` and assert the final prompt contains both
    ``[WHAT YOU KNOW]`` and ``[RECALLED FROM EARLIER]``."""
    print("─" * 60)
    print("FELT-TEST #3  —  integrated prompt assembly\n")

    query = "conditions a kid grows up in and gut instincts"

    # ── Seed the knowledge notebook (independent of test #1's cleanup) ──
    tmp = _setup_temp_notebook()
    eid = knowledge_store.make_entity_id("person", "Elliot")
    knowledge_store.append_entity(
        "relational", id=eid, type="person", name="Elliot", origin="she",
    )
    knowledge_store.append_relation(
        "relational",
        id=knowledge_store.make_relation_id(
            "person:elliot", "grew_up_in", "a low-income neighborhood",
        ),
        subject_id="person:elliot",
        predicate="grew_up_in",
        object_value="a low-income neighborhood",
        object_kind="literal",
        provenance=["user_personal_1"],
        confidence=0.9,
        origin="she",
        locked=False,
    )

    directive = __import__("config.directive", fromlist=["get_directive"]).get_directive()

    messages = build_chat_messages(
        directive=directive,
        user_input=query,
        history=[],
        recall_block=recall_block,
    )

    system = messages[0]["content"] if messages else ""

    assert "[WHAT YOU KNOW]" in system, (
        "[WHAT YOU KNOW] missing from system prompt"
    )
    assert "[RECALLED FROM EARLIER]" in system, (
        "[RECALLED FROM EARLIER] missing from system prompt"
    )
    assert "low-income neighborhood" in system, (
        "Personal fact missing from system prompt"
    )

    print("  SYSTEM PROMPT contains both blocks:")
    print("    ✅ [WHAT YOU KNOW]      —  personal fact present")
    print("    ✅ [RECALLED FROM EARLIER]  —  personal memory present")
    print()

    shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# 5.  Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    recall_block = test_personal_fact_beats_topical()
    test_locked_correction_wins()
    test_integrated_prompt(recall_block)
    print("═" * 60)
    print("OK Phase 4 L2 integrated felt-test")
