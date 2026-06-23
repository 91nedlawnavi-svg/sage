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

from config.settings import KNOWLEDGE_ENABLED, KNOWLEDGE_FACT_MIN_SIM
from cognition.knowledge_reconcile import reconcile_notebook
from memory.semantic_recall import _cosine

# Split on any non-alphanumeric sequence, discarding empty strings.
_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")

# Singular first-person subject/possessive pronouns. When any of these appear
# in the user's message, the user is talking about themselves — i.e. Elliot.
# Object forms ("me") are deliberately excluded so imperative phrasings like
# "tell me about Sage" do not pollute the query with personal facts.
_FIRST_PERSON_PRONOUNS = frozenset({"i", "my", "mine", "myself"})
_FIRST_PERSON_OBJECT_PRONOUNS = frozenset({"me"})

_BROAD_MEMORY_WORDS = frozenset({
    "remember", "memory", "memories", "know", "facts", "personal", "about",
    "doing",
})

_BROAD_SELF_MEMORY_WORDS = frozenset({
    "remember", "memory", "memories", "know", "facts", "personal",
})

_DETAIL_STOPWORDS = frozenset({
    "a", "an", "the", "of", "to", "for", "with", "about", "by", "as",
    "in", "on", "at",
})

_PREDICATE_QUERY_ALIASES = {
    "lives_in": {"live", "lives", "where"},
    "grew_up_in": {"grow", "grows", "grew", "childhood", "kid"},
    "born_in": {"born", "birth", "where"},
    "works_on": {"work", "works", "working"},
    "works_at": {"work", "works", "working"},
    "studied": {"study", "studies", "studied"},
    "studied_at": {"study", "studies", "studied", "school"},
    "owns": {"own", "owns", "owned"},
    "affected_by": {"affected", "shaped", "because"},
}


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


def _relation_detail_words(rel: dict, entities: dict) -> set[str]:
    """Words from predicate + object, excluding the subject name."""
    obj = _object_text(entities, rel.get("object", {}))
    pred = rel.get("predicate") or ""
    words = (_predicate_words(rel) | _word_set(obj)) - _DETAIL_STOPWORDS
    if pred in ("friend_of", "knows", "has_relationship_with"):
        words.add("close")
    return words


def _predicate_words(rel: dict) -> set[str]:
    """Words from the predicate only."""
    pred = rel.get("predicate") or ""
    words = _word_set(pred.replace("_", " ")) - _DETAIL_STOPWORDS
    words |= _PREDICATE_QUERY_ALIASES.get(pred, set())
    if pred in ("friend_of", "knows", "has_relationship_with"):
        words.add("close")
    return words


def _broad_personal_query(input_words: set[str], mentioned_ids: set[str]) -> bool:
    """True when the user is asking for broad remembered facts about himself."""
    if "person:elliot" not in mentioned_ids:
        return False
    return bool(input_words & _BROAD_MEMORY_WORDS)


# ── entity mention detection ────────────────────────────────────────────

def _mentioned_entity_ids(user_input: str, entities: dict) -> set[str]:
    """Return set of entity ids whose name, alias, or id-tail appears in input.

    Only literally-named entities are included by default. As a special case,
    when the input contains a singular first-person subject/possessive pronoun
    (``i``/``my``/``mine``/``myself``) and ``person:elliot`` exists in the
    notebook, that entity is injected — the user is talking about himself, so
    his personal facts should have a path to surface for paraphrased queries
    that name neither him nor any fact keyword. Object forms ("me") are
    excluded so requests like "tell me about Sage" stay clean.
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

    # First-person pronoun: user is talking about Elliot himself. Without this,
    # paraphrased personal queries (no name + no fact keyword) have no path to
    # surface — _relevance_key boosts Elliot facts but _is_relevant filters
    # them out first, a dead path. This injection closes that gap.
    if (input_words & _FIRST_PERSON_PRONOUNS) and "person:elliot" in entities:
        mentioned.add("person:elliot")
    if (
        (input_words & _FIRST_PERSON_OBJECT_PRONOUNS)
        and (input_words & _BROAD_SELF_MEMORY_WORDS)
        and "person:elliot" in entities
    ):
        mentioned.add("person:elliot")

    return mentioned


# ── relevance filter and scorer ─────────────────────────────────────────

def _is_relevant(
    rel: dict,
    mentioned_ids: set[str],
    input_words: set[str],
    entities: dict,
    *,
    any_elliot_detail_overlap: bool = False,
) -> bool:
    """Return True if *rel* should be included for the current user_input.

    A fact is relevant when:
      1. Its object entity was literally named in the input, OR
      2. Its subject was named and the query is broad ("what do you remember
         about me?") or overlaps predicate/object details, OR
      3. It shares at least 2 word tokens with the input (lexical overlap).

    When a first-person pronoun injects Elliot but NO Elliot fact has any
    detail/predicate overlap with the query, the query is inherently broad
    ("how did my upbringing shape me", "what do you know about my past") —
    all Elliot facts are admitted so paraphrased personal queries aren't
    gated behind hand-written alias lists.  When at least one Elliot fact
    *does* have detail overlap, the query is specific and only matching
    facts are surfaced ("who was I close with").
    """
    subject_id = rel.get("subject_id", "")
    obj = rel.get("object", {})
    obj_id = obj.get("value", "") if obj.get("kind") == "entity" else ""

    # Entity literal mention: an entity name/alias was literally in the input
    detail_overlap = bool(input_words & _relation_detail_words(rel, entities))
    predicate_overlap = bool(input_words & _predicate_words(rel))
    broad_query = bool(input_words & _BROAD_MEMORY_WORDS)

    if subject_id in mentioned_ids and (
        subject_id != "person:elliot"
        or _broad_personal_query(input_words, mentioned_ids)
        or detail_overlap
        or (subject_id == "person:elliot" and not any_elliot_detail_overlap)
    ):
        return True
    if obj_id and obj_id in mentioned_ids and (broad_query or predicate_overlap):
        return True

    # Lexical overlap: at least 2 shared word tokens
    fw = _word_set(_fact_text(rel, entities))
    if len(input_words & fw) >= 2:
        return True

    return False


def _relevance_key(rel, mentioned_ids, input_words, entities, similarity=0.0):
    """Sort key (descending): most relevant first.

    Terms (highest to lowest precedence):
      1. Authority: locked (2) > elliot (1) > she (0)
      2. Entity-match boost
      3. Semantic similarity (when available; 0.0 on fallback path)
      4. Lexical word-overlap count
      5. Model confidence
      6. Recency
    """
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
    # Always boost facts about Elliot.
    if subject_id == "person:elliot":
        entity_match += 1

    # Semantic similarity (0.0 when query_embedding not provided)
    sim_key = round(similarity, 3)

    # Lexical overlap (all word tokens)
    fw = _word_set(_fact_text(rel, entities))
    lexical = len(input_words & fw)

    try:
        confidence = float(rel.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return (authority, entity_match, sim_key, lexical, confidence, rel.get("ts") or "")


# ── public helpers: structured access + formatted block ─────────────────

def select_relevant_relations(
    notebook: str = "relational",
    *,
    user_input: str | None = None,
    max_facts: int = 12,
    query_embedding: list[float] | None = None,
    fact_vectors: dict[str, list[float]] | None = None,
) -> list[dict]:
    """Return targeted relation records (with provenance) for the current user_input.

    Gate-aware: returns ``[]`` when ``KNOWLEDGE_ENABLED`` is False.

    When *query_embedding* and *fact_vectors* are both provided, facts whose
    semantic cosine similarity reaches ``KNOWLEDGE_FACT_MIN_SIM`` are admitted
    as *additional* candidates alongside the lexical/entity/pronoun matches,
    so a fact with no word overlap still surfaces if it is meaning-related.

    Contract: retrieves the query vector from the caller, never calling e5
    itself. When either parameter is absent, behaviour is exactly the
    lexical/entity/pronoun baseline (no semantic admission).

    Relations retain full provenance, so callers can extract source keys for
    recall boost. Uses the exact same targeting/ranking logic as
    ``build_knowledge_block()``.
    """
    if not KNOWLEDGE_ENABLED:
        return []
    try:
        view = reconcile_notebook(notebook)
        entities = view.get("entities", {})
        relations = view.get("relations", {})
        if not relations:
            return []

        if user_input is not None:
            mentioned_ids = _mentioned_entity_ids(user_input, entities)
            input_words = _word_set(user_input)

            # ── 1. Pre-scan: does any Elliot fact match a query detail? ─
            # When at least one Elliot-subject fact overlaps predicate or
            # object detail words, the query is specific; otherwise it is
            # broad — all Elliot facts are admitted so paraphrased queries
            # (e.g. "how did my upbringing shape me") aren't blocked by an
            # incomplete hand-written alias list.
            _any_elliot_detail = (
                "person:elliot" in mentioned_ids
                and any(
                    bool(input_words & _relation_detail_words(rel, entities))
                    or bool(input_words & _predicate_words(rel))
                    for rel in relations.values()
                    if rel.get("subject_id") == "person:elliot"
                )
            )

            # ── 2. Lexical / entity / pronoun candidates ─────────────
            lexical_candidates = [
                rel
                for rel in relations.values()
                if _is_relevant(
                    rel, mentioned_ids, input_words, entities,
                    any_elliot_detail_overlap=_any_elliot_detail,
                )
            ]

            # ── 2. Semantic similarity map ──────────────────────────
            # Pre-compute once per fact, reused for admission + ranking.
            similarities: dict[str, float] = {}
            semantic_available = (
                query_embedding is not None and fact_vectors is not None
            )
            if semantic_available:
                for rid, vec in fact_vectors.items():
                    sim = _cosine(query_embedding, vec)
                    if sim >= 0.0:
                        similarities[rid] = sim

            # ── 3. Admit semantically-matched facts ─────────────────
            admitted: list[dict] = list(lexical_candidates)
            seen_ids = {rel.get("id") for rel in lexical_candidates if rel.get("id")}
            if semantic_available:
                for rel in relations.values():
                    rid = rel.get("id", "")
                    if rid in seen_ids:
                        continue
                    sim = similarities.get(rid, 0.0)
                    if sim >= KNOWLEDGE_FACT_MIN_SIM:
                        admitted.append(rel)
                        seen_ids.add(rid)

            if not admitted:
                return []
            return sorted(
                admitted,
                key=lambda r: _relevance_key(
                    r, mentioned_ids, input_words, entities,
                    similarity=similarities.get(r.get("id", ""), 0.0),
                ),
                reverse=True,
            )[:max_facts]
        else:
            return sorted(relations.values(), key=_rank_key, reverse=True)[:max_facts]
    except Exception:
        return []


def build_knowledge_block(
    notebook: str = "relational",
    *,
    user_input: str | None = None,
    relations: list[dict] | None = None,
    max_facts: int = 12,
    max_chars: int = 1200,
) -> str | None:
    """Return the ``[WHAT YOU KNOW]`` block targeted to *user_input*, or None.

    Gated by KNOWLEDGE_ENABLED so it stays inert until first-light is armed.
    When *user_input* is None, falls back to the old global top-N behaviour
    (no targeting).

    When *relations* is supplied, it is treated as the already-selected fact
    set for this turn. This keeps chat-time semantic fact selection aligned
    with the formatted prompt block instead of re-running a lexical-only
    selection pass.
    """
    if not KNOWLEDGE_ENABLED:
        return None
    try:
        if relations is None:
            # Delegate targeting to the shared helper so recall boost and the
            # formatted block stay aligned on the same fact selection.
            ranked = select_relevant_relations(
                notebook, user_input=user_input, max_facts=max_facts
            )
        else:
            ranked = relations[:max_facts]
        if not ranked:
            return None

        # Reconcile entities separately for name resolution in formatting.
        view = reconcile_notebook(notebook)
        entities = view.get("entities", {})

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

    # Elliot knows Maya
    knowledge_store.append_relation(
        "relational",
        id=knowledge_store.make_relation_id(elliot, "knows", maya),
        subject_id=elliot, predicate="knows",
        object_value=maya, object_kind="entity",
        origin="she", confidence=0.8)

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
    # Regression guard: "me" is an object pronoun here, NOT first-person
    # subject — Elliot's personal facts (grew_up_in, likes ramen) must NOT
    # leak into a Sage query via the pronoun path.
    assert "grew up in" not in block, (
        f"'tell me about Sage' must not surface Elliot personal facts. "
        f"Got:\n{block}"
    )

    # ================================================================
    # 5. No-match query → None
    # ================================================================
    block = build_knowledge_block("relational", user_input="what is the weather like today")
    assert block is None, f"no relevant facts should return None"

    # ================================================================
    # 6. First-person pronoun → Elliot facts surface (paraphrased query)
    # ================================================================
    # The pronoun path is what gives paraphrased personal queries a way in.
    # "who was I close with" names neither Elliot nor Maya, but "close" maps
    # to durable relationship predicates like knows/friend_of. It should
    # surface relationship facts, not every fact about Elliot.
    block = build_knowledge_block(
        "relational", user_input="who was I close with back then",
    )
    assert block is not None, (
        f"first-person pronoun query should surface Elliot facts. Got None"
    )
    assert "Elliot" in block, f"Elliot facts should surface for 'I' query"
    assert "Maya" in block, (
        f"a personal relationship fact should surface via the pronoun path. "
        f"Got:\n{block}"
    )
    assert "grew up in" not in block, (
        f"unrelated Elliot facts should not leak into a close-relationship query. "
        f"Got:\n{block}"
    )

    # ================================================================
    # 7. Locked correction still wins (Jakarta > Paris) with targeting
    # ================================================================
    block = build_knowledge_block("relational", user_input="where does Elliot live now")
    assert block is not None
    assert "Jakarta" in block, f"locked Jakarta should beat Paris"
    assert "Paris" not in block, f"Paris should be suppressed by reconcile"
    assert "(you confirmed)" in block, f"locked fact should have confirmed marker"

    # ================================================================
    # 8. Empty notebook + targeting → None
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
    # 9. Global mode (no user_input) — backwards compat: returns block
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

    # ================================================================
    # 10. Semantic admission (Task D) — high-cosine no-overlap surfaces
    # ================================================================
    # Re-initialise a fresh notebook with one fact that has NO lexical
    # overlap with the test query.  The semantic admission path (via
    # query_embedding + fact_vectors) should pull it in.
    tmp_d = Path(tempfile.mkdtemp())
    knowledge_store._NOTEBOOK_PATHS["semtest"] = (
        tmp_d / "sem_entities.jsonl",
        tmp_d / "sem_relations.jsonl",
    )
    eid = knowledge_store.make_entity_id("person", "Elliot")
    knowledge_store.append_entity("semtest", id=eid, type="person", name="Elliot", origin="she")

    # Add a socioeconomic fact (the Phase 3 L1-miss scenario)
    knowledge_store.append_relation(
        "semtest",
        id=knowledge_store.make_relation_id(eid, "grew_up_in", "a low-income neighborhood"),
        subject_id=eid, predicate="grew_up_in",
        object_value="a low-income neighborhood", object_kind="literal",
        origin="she", confidence=0.9)
    # Add a tangentially-related fact that shares "social" with the query
    knowledge_store.append_relation(
        "semtest",
        id=knowledge_store.make_relation_id(eid, "values", "social equality"),
        subject_id=eid, predicate="values",
        object_value="social equality", object_kind="literal",
        origin="she", confidence=0.6)
    # Add an unrelated fact
    knowledge_store.append_relation(
        "semtest",
        id=knowledge_store.make_relation_id(eid, "likes", "ramen"),
        subject_id=eid, predicate="likes",
        object_value="ramen", object_kind="literal",
        origin="she", confidence=0.5)

    sem_view = reconcile_notebook("semtest")
    sem_rels = sem_view["relations"]

    # NOTE: grew_up_in is in the grounding map — the reconciled id is computed
    # over the entity ref, not the raw literal.  Use the reconciled view to get
    # the correct id for each fact's vector key.
    grow_up_id = next(
        rid for rid, r in sem_rels.items()
        if r.get("predicate") == "grew_up_in"
    )
    vals_id = next(
        rid for rid, r in sem_rels.items()
        if r.get("predicate") == "values"
    )
    likes_id = next(
        rid for rid, r in sem_rels.items()
        if r.get("predicate") == "likes"
    )

    # -- 10a: semantic admission with mock vectors --
    # Query vector that has high cosine with the socioeconomic fact only.
    # A unit vector [1,0] admits fact with vector [1,0]; others with [0,1] stay out.
    sv = [1.0, 0.0]
    fv = {}
    for rid in sem_rels:
        fv[rid] = [1.0, 0.0] if rid == grow_up_id else [0.0, 1.0]

    sem = select_relevant_relations(
        "semtest",
        user_input="zzzzyxwvu nonsensical query with zero token overlap",
        query_embedding=sv,
        fact_vectors=fv,
    )
    assert len(sem) >= 1, \
        f"semantic admission should bring in at least one fact, got {len(sem)}"
    assert any(r["id"] == grow_up_id for r in sem), \
        "socioeconomic fact should be admitted via semantic match"
    # The "values social equality" fact shares NO token with the gobbledygook
    # query AND has a low-cosine vector — it must NOT be admitted.
    assert not any(r["id"] == vals_id for r in sem), \
        "low-cosine fact must NOT be admitted"

    # -- 10b: same query, no query_embedding → purely lexical fallback --
    sem2 = select_relevant_relations(
        "semtest",
        user_input="zzzzyxwvu nonsensical query with zero token overlap",
        # no query_embedding, no fact_vectors
    )
    assert len(sem2) == 0, \
        f"no semantic path should return empty, got {len(sem2)}"

    # -- 10c: authority still dominates ranking --
    # Add a high-authority fact with a low-cosine vector; it should rank above
    # a low-authority fact even though the low-authority one has higher cosine.
    knowledge_store.append_relation(
        "semtest",
        id=knowledge_store.make_relation_id(eid, "lives_in", "Jakarta"),
        subject_id=eid, predicate="lives_in",
        object_value="Jakarta", object_kind="literal",
        origin="elliot", locked=True, confidence=1.0)
    sem_view2 = reconcile_notebook("semtest")
    # Use the reconciled (grounded) id for the locked fact
    locked_id = next(
        rid for rid, r in sem_view2["relations"].items()
        if r.get("predicate") == "lives_in" and r.get("locked")
    )
    # For the locked fact to be admitted semantically it also needs
    # sim >= KNOWLEDGE_FACT_MIN_SIM (0.80).  Give it 0.90 — above
    # threshold but below the socioeconomic fact's 1.0.  Both enter,
    # and the locked fact's higher authority dominates the ranking.
    fv2 = {}
    for rid in sem_view2["relations"]:
        if rid == locked_id:
            fv2[rid] = [0.9, 0.43589]   # cos ≈ 0.90 with sv=[1,0]
        elif rid == grow_up_id:
            fv2[rid] = [1.0, 0.0]       # cos = 1.00 with sv=[1,0]
        else:
            fv2[rid] = [0.0, 1.0]       # cos = 0.00
    sem3 = select_relevant_relations(
        "semtest",
        user_input="zzzzyxwvu nonsense",
        query_embedding=sv,
        fact_vectors=fv2,
    )
    assert len(sem3) >= 1
    # The socioeconomic fact (cos=1.0, authority=0) and the locked Jakarta
    # fact (cos=0.9, authority=2) are both admitted.  Authority dominates
    # similarity in _relevance_key, so the locked fact ranks first.
    assert sem3[0]["id"] == locked_id, \
        f"authority (locked) should dominate semantic similarity; " \
        f"expected {locked_id} first, got {sem3[0]['id']}"

    shutil.rmtree(tmp_d)

    print("OK")
