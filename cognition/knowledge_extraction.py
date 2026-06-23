"""
Knowledge extraction — Phase 4 Layer 2, Step 2.

Derives entity + relation *candidates* from source text (conversation turns for
the relational notebook; reflections/findings for the interior notebook) using
the NIM chat model, and shapes them into records the knowledge_store can append.

Scope of THIS module (extraction only):
  - Build the LLM prompt, call nim_complete, parse + validate the JSON, and
    normalize the result into store-ready entity/relation records
    (origin="she", with provenance back to the source keys).
  - It does NOT do e5 entity de-duplication, an incremental processed-cursor,
    or any heartbeat wiring -- those are later sub-steps. Nothing here is
    imported by the live app yet; the module is inert until wired in.

Everything degrades silently: a model failure or unparseable output returns
None so callers can retry without advancing their cursor; structurally valid
but empty extractions produce empty candidate lists.
"""

from __future__ import annotations

import json
import re
from typing import Any

from memory.knowledge_store import make_entity_id, make_relation_id
from utils.logger import log, warning

# Entity types we recognize; anything else collapses to "topic".
VALID_ENTITY_TYPES = ("person", "place", "project", "org", "topic", "event")

# Extraction model settings -- deliberately low temperature for stable,
# factual extraction over a transcript.
EXTRACTION_TEMPERATURE = 0.2
EXTRACTION_MAX_TOKENS = 1024

# How much of each turn to feed the model, and how many turns per batch.
MAX_CHARS_PER_TURN = 600
MAX_TURNS_PER_BATCH = 12

# Relations below this model-reported confidence are not persisted. Conservative
# floor for first-light; raise after inspecting a real rebuild. Facts where the
# model omits confidence default to 0.5 and are kept.
MIN_PERSIST_CONFIDENCE = 0.5

# Canonical anchor for the human user in the relational notebook. First-person
# references and "Elliot" all resolve to this stable id so personal facts
# accrete on one node (the L1 miss this layer exists to fix).
ELLIOT_ENTITY_ID = "person:elliot"
_ELLIOT_ALIASES = frozenset({"elliot", "i", "me", "my", "myself", "user"})
# Name tokens that mark a possessive / self-referential ENTITY name, e.g.
# "Elliot's old school" or "my old school". The clean entity ("old school") is
# extracted separately, so we refuse the garbled possessive duplicate at
# registration time. Deliberately narrower than _ELLIOT_ALIASES: we exclude
# "i"/"me"/"user" to avoid false positives on legitimate names (e.g. "User
# Guide"); only the clear possessive/self markers belong here.
_SELF_REF_NAME_TOKENS = frozenset({"elliot", "my", "myself"})


# ── small normalizers ──────────────────────────────────────────

def _slug_key(name: str) -> str:
    """Stable lookup key for an entity name (mirrors the store's slug rule)."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _norm_predicate(pred: str) -> str:
    """Lowercase; collapse whitespace/hyphens to single underscores; strip junk."""
    pred = (pred or "").strip().lower()
    pred = re.sub(r"[\s\-]+", "_", pred)
    pred = re.sub(r"[^a-z0-9_]", "", pred)
    return pred.strip("_")


# ── controlled predicate vocabulary ───────────────────────────

# Canonical predicates the extractor aims to produce. These represent durable
# relationship types; everything else is aliased or generic-mapped.
_CANONICAL_PREDICATES = frozenset({
    "grew_up_in", "born_in", "lives_in",
    "works_on", "works_at", "studied", "studied_at",
    "knows", "friend_of", "sibling_of", "parent_of", "child_of",
    "prefers", "likes", "dislikes", "believes", "values",
    "speaks", "owns", "has_role",
    "affected_by", "related_to",
    "interested_in", "skilled_at",
})

# Exact predicate normalizations (verbose model output -> canonical).
_PREDICATE_ALIASES = {
    "grew_up": "grew_up_in",
    "works_as": "has_role",
    "works_in": "works_on",
    "lives_at": "lives_in",
    "knows_about": "knows",
    "knows_how_to": "skilled_at",
    "is_friends_with": "friend_of",
    "is_sibling_of": "sibling_of",
    "is_parent_of": "parent_of",
    "is_child_of": "child_of",
    "is_good_at": "skilled_at",
    "good_at": "skilled_at",
    "has_skill": "skilled_at",
    "has_experience_in": "skilled_at",
    "interested_in": "interested_in",
    "is_interested_in": "interested_in",
    "cares_about": "values",
    "enjoys": "likes",
    "loves": "likes",
    "hates": "dislikes",
    "thinks": "believes",
    "studies": "studied",
    "has_role_as": "has_role",
}

# Generic fallback for overly long predicates (>3 tokens) that match no alias.
_GENERIC_CATCHALL = "related_to"

# Max underscore-separated tokens before a predicate is considered "overly long".
_MAX_PREDICATE_TOKENS = 3


def _alias_by_pattern(pred: str) -> str | None:
    """Check pattern-based aliases against a normalized predicate.

    Patterns cover the model's most common multi-word verbosity patterns
    that would otherwise produce freeform (>3 token) predicates.
    """
    # *_due_to -> affected_by  (e.g. had_unpleasant_experiences_due_to)
    if pred.endswith("_due_to"):
        return "affected_by"
    # due_to_* -> affected_by
    if pred.startswith("due_to_"):
        return "affected_by"
    # affected_by_* -> affected_by
    if pred.startswith("affected_by_"):
        return "affected_by"
    # had_*_experiences -> affected_by
    if pred.startswith("had_") and pred.endswith("_experiences"):
        return "affected_by"
    return None


def _normalize_predicate(raw: str) -> str:
    """Full predicate normalization: normalize, alias-map, then enforce length limit.

    Steps:
      1. Apply ``_norm_predicate`` (lowercase, snake_case, strip junk).
      2. Check exact alias map (``_PREDICATE_ALIASES``).
      3. Check pattern-based aliases (``_alias_by_pattern``).
      4. If still unmapped and >3 underscore-separated tokens, map to the
         generic catchall (``_GENERIC_CATCHALL`` — ``related_to``).

    Logs every normalization that changes the predicate from its normalized form.

    Callers should use this instead of ``_norm_predicate`` directly.
    """
    original = (raw or "").strip()
    pred = _norm_predicate(original)
    if not pred:
        return pred

    # --- exact alias ---
    canonical = _PREDICATE_ALIASES.get(pred)
    if canonical is None:
        canonical = _alias_by_pattern(pred)

    if canonical:
        if pred != canonical:
            log("knowledge_extraction", "predicate-normalized",
                original=original, normalized=pred, canonical=canonical)
        return canonical

    # --- length guard: overly long predicates with no alias -> generic ---
    tokens = pred.split("_")
    if len(tokens) > _MAX_PREDICATE_TOKENS:
        log("knowledge_extraction", "predicate-generic-mapped",
            original=original, normalized=pred, tokens=len(tokens),
            canonical=_GENERIC_CATCHALL)
        return _GENERIC_CATCHALL

    return pred


def _norm_type(t: str) -> str:
    t = (t or "").strip().lower()
    return t if t in VALID_ENTITY_TYPES else "topic"


def _clamp_conf(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, f))


def _as_text(v: Any) -> str:
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float)):
        return str(v)
    return ""


# ── durability / quality filters ───────────────────────────────

# Leading predicate tokens that denote a transient state, mood, one-off action,
# or intention rather than a durable fact. Matched against the predicate's first
# underscore-delimited token (so "wants_to_help" -> "wants").
_TRANSIENT_PRED_TOKENS = frozenset({
    "feel", "feels", "felt", "feeling",
    "want", "wants", "wanted", "wish", "wishes", "wishing",
    "hope", "hopes", "hoping",
    "plan", "plans", "planning", "intend", "intends", "intending",
    "ask", "asks", "asked", "asking",
    "watch", "watches", "watched", "watching",
    "need", "needs", "needed",
    "worry", "worries", "worried",
    "concern", "concerns", "concerned",
})

# Trailing object tokens that signal a truncated / dangling fragment.
_DANGLING_OBJECT_TAIL = frozenset({
    "about", "with", "to", "of", "for", "and", "or", "the", "a", "an",
    "in", "on", "at", "from", "by",
})


def _is_transient_predicate(pred: str) -> bool:
    """True if the predicate's leading token denotes a transient state/action."""
    head = pred.split("_", 1)[0]
    return head in _TRANSIENT_PRED_TOKENS


def _is_low_quality_object(obj: str) -> bool:
    """True if a literal object looks like a fragment, dangling word, or empty."""
    o = (obj or "").strip().lower()
    if len(o) < 3:
        return True
    tokens = [t for t in re.split(r"[^a-z0-9]+", o) if t]
    if not tokens:
        return True
    if tokens[-1] in _DANGLING_OBJECT_TAIL:
        return True
    return False


# ── prompt ──────────────────────────────────────────────

_SCHEMA_BLOCK = (
    'Return STRICT JSON only -- no prose, no code fences -- in exactly this shape:\n'
    '{\n'
    '  "entities": [{"name": "...", "type": "person|place|project|org|topic|event", "aliases": ["..."]}],\n'
    '  "relations": [{"subject": "...", "predicate": "snake_case_verb", "object": "...", "object_type": "entity|literal", "confidence": 0.0}]\n'
    '}\n'
    'Rules:\n'
    '- "type" is one of person/place/project/org/topic/event.\n'
    '- "subject" and "object" are entity names. Use "Elliot" for the human user. '
    'Never use a possessive form of the subject as the object: write the place or '
    'thing itself (e.g. "Lincoln High"), not "Elliot\'s school".\n'
    '- "object_type" is "entity" when the object is one of the listed entities, '
    'otherwise "literal" for a concrete standalone value (a place, an institution, '
    'a field of study, a relationship). A literal must be a complete noun phrase, '
    'not a sentence fragment and not a dangling word.\n'
    '- "predicate" is a short snake_case phrase naming a LASTING property or '
    'relationship. Prefer this vocabulary when it fits: grew_up_in, born_in, '
    'lives_in, works_on, works_at, studied, studied_at, knows, friend_of, '
    'sibling_of, parent_of, child_of, prefers, likes, dislikes, believes, values, '
    'speaks, owns, has_role.\n'
    '- "confidence" is 0-1 for how strongly the transcript supports a durable fact.\n'
    'Do NOT extract momentary feelings or moods, one-off intentions or plans '
    '("wants to", "is going to", "asked about"), single past actions, or anything '
    'tied to a specific day ("tomorrow", "tonight"). If nothing durable is '
    'present, return {"entities": [], "relations": []}.'
)

_RELATIONAL_FOCUS = (
    "You read a transcript between Elliot (the human) and Sage (the AI). Extract "
    "only DURABLE facts -- things that will still be true next month -- about "
    "Elliot and the people, places, projects, organizations, and topics in HIS "
    "life: personal history, where he grew up, relationships, work, what he "
    "studies, stable preferences and beliefs, lasting circumstances. Do NOT record "
    "how he feels right now, what he wants or plans to do, what he asked about, or "
    "events pinned to a particular day -- those are transient. Extract a fact only "
    "if you would expect it to still matter weeks from now. IGNORE anything Sage "
    "says about itself, and ignore small talk."
)

_INTERIOR_FOCUS = (
    "You read Sage's own private reflections and research findings. Extract the "
    "topics, concepts, thinkers, and questions SHE has been exploring on her own, "
    "and how they relate. This is Sage's evolving intellectual interior. Do NOT "
    "extract facts about Elliot or the user here -- only Sage's own lines of "
    "inquiry and the ideas she is connecting."
)


def _format_turns(turns: list[dict]) -> str:
    """Render a batch of source records into a compact labelled transcript."""
    lines: list[str] = []
    for t in turns[:MAX_TURNS_PER_BATCH]:
        role = (t.get("role") or t.get("kind") or "note").strip()
        content = _as_text(t.get("content") or t.get("text"))
        if not content:
            continue
        if len(content) > MAX_CHARS_PER_TURN:
            content = content[:MAX_CHARS_PER_TURN] + "…"
        label = {"user": "Elliot", "assistant": "Sage"}.get(role, role)
        lines.append(f"[{label}] {content}")
    return "\n".join(lines)


def build_extraction_prompt(turns: list[dict], *, notebook: str = "relational") -> tuple[str, str]:
    """Build (system, user) messages for the extraction call. Pure + testable."""
    focus = _INTERIOR_FOCUS if notebook == "interior" else _RELATIONAL_FOCUS
    system = (
        "You are a precise knowledge-extraction component inside Sage, a local AI "
        "companion. " + focus + "\n\n" + _SCHEMA_BLOCK
    )
    transcript = _format_turns(turns)
    user = f"TRANSCRIPT:\n{transcript}\n\nReturn the JSON now."
    return system, user


# ── parsing ────────────────────────────────────────────

def parse_extraction(raw: str | None) -> dict | None:
    """Parse and validate the model's JSON extraction output.

    Returns the parsed dict on success (including valid empty extractions like
    ``{"entities": [], "relations": []}``), or **None** when the output is
    malformed — invalid JSON, not a dict, missing required keys, or wrong
    value types. Callers should treat None as a retryable failure and must NOT
    advance any processing cursor.

    Validation requirements:
    - top-level value must be a dict
    - must contain both ``"entities"`` and ``"relations"`` keys
    - both values must be lists
    """
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if "entities" not in data or "relations" not in data:
        return None
    if not isinstance(data["entities"], list) or not isinstance(data["relations"], list):
        return None
    return data


# ── candidate shaping ──────────────────────────────────────

def candidates_from_parsed(
    parsed: dict,
    source_keys: list[str],
    *,
    anchor_elliot: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Normalize parsed model output into store-ready candidate records.

    Returns (entity_records, relation_records). Entity records carry the kwargs
    for knowledge_store.append_entity; relation records the kwargs for
    append_relation (minus origin, which the persister stamps as "she").
    Pure + testable -- no I/O.
    """
    if not isinstance(parsed, dict):
        return [], []

    entities_out: list[dict] = []
    name_to_id: dict[str, str] = {}
    id_to_entity: dict[str, dict] = {}

    if anchor_elliot:
        for alias in _ELLIOT_ALIASES:
            name_to_id[alias] = ELLIOT_ENTITY_ID

    def _register(name: str, type_: str, aliases: list | None) -> str | None:
        name = (name or "").strip()
        if not name:
            return None
        key = _slug_key(name)
        if key in _ELLIOT_ALIASES or name_to_id.get(key) == ELLIOT_ENTITY_ID:
            return ELLIOT_ENTITY_ID
        # Refuse possessive/self-referential entity names ("Elliot's old school",
        # "my old school"). Returning None keeps it out of the entity table; any
        # relation that referenced it falls through to the literal branch and is
        # caught by the literal self-ref guard there. This extends the self-ref
        # protection from literal objects to entity nodes.
        name_tokens = {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t}
        if name_tokens & _SELF_REF_NAME_TOKENS:
            return None
        if key in name_to_id:
            return name_to_id[key]
        eid = make_entity_id(type_, name)
        rec = {
            "id": eid,
            "type": type_,
            "name": name,
            "aliases": [a.strip() for a in (aliases or []) if isinstance(a, str) and a.strip()],
        }
        name_to_id[key] = eid
        id_to_entity[eid] = rec
        entities_out.append(rec)
        return eid

    for e in parsed.get("entities") or []:
        if isinstance(e, dict):
            _register(e.get("name"), _norm_type(e.get("type")), e.get("aliases"))

    relations_out: list[dict] = []
    referenced_ids: set[str] = set()

    for r in parsed.get("relations") or []:
        if not isinstance(r, dict):
            continue
        subj = _as_text(r.get("subject"))
        pred = _normalize_predicate(r.get("predicate"))
        obj = _as_text(r.get("object"))
        if not subj or not pred or not obj:
            continue
        # Durability gate: drop transient states, moods, one-off actions, plans.
        if _is_transient_predicate(pred):
            continue
        # Confidence gate: drop facts the model itself is unsure about.
        conf = _clamp_conf(r.get("confidence"))
        if conf < MIN_PERSIST_CONFIDENCE:
            continue
        subj_id = name_to_id.get(_slug_key(subj))
        if subj_id is None:
            # Subject was not among the listed entities -- skip to avoid garbage
            # nodes with unknown type. (A later e5 step can be smarter.)
            continue
        obj_id = name_to_id.get(_slug_key(obj))
        if (r.get("object_type") or "").strip().lower() == "entity" and obj_id:
            object_kind, object_value = "entity", obj_id
            referenced_ids.add(obj_id)
        else:
            object_kind, object_value = "literal", obj
        # Quality gate for literal objects: reject fragments / dangling words and
        # self-referential objects that merely restate the subject.
        if object_kind == "literal":
            if _is_low_quality_object(object_value):
                continue
            if subj_id == ELLIOT_ENTITY_ID and (
                {t for t in re.split(r"[^a-z0-9]+", object_value.lower()) if t}
                & _ELLIOT_ALIASES
            ):
                continue
        referenced_ids.add(subj_id)
        relations_out.append(
            {
                "id": make_relation_id(subj_id, pred, object_value),
                "subject_id": subj_id,
                "predicate": pred,
                "object_value": object_value,
                "object_kind": object_kind,
                "confidence": conf,
                "provenance": list(source_keys),
            }
        )

    # Ensure every referenced entity id has a record. Only Elliot can be
    # referenced without having been listed; synthesize his node if so.
    have_ids = {e["id"] for e in entities_out}
    if ELLIOT_ENTITY_ID in referenced_ids and ELLIOT_ENTITY_ID not in have_ids:
        entities_out.insert(
            0, {"id": ELLIOT_ENTITY_ID, "type": "person", "name": "Elliot", "aliases": []}
        )

    # Drop entities that ended up referenced by nothing AND were Elliot-only noise?
    # No -- standalone entities are legitimate (they may gain edges later).
    return entities_out, relations_out


# ── orchestration ─────────────────────────────────────────

async def extract_from_turns(turns, client, *, notebook: str = "relational"):
    """Run one extraction pass over a batch of source records.

    Returns (entity_records, relation_records) when the model replied (the lists
    may be empty if no durable facts were found), or None when:
    - the model/infra failed (``nim_complete`` returned None or raised)
    - the model returned malformed output (invalid JSON or wrong shape)

    Callers must treat None as a retryable failure and NOT mark these turns as
    processed, so they are re-tried on the next beat. Does not persist — call
    persist_candidates() to write.
    """
    if not turns:
        return [], []
    try:
        from models.inference.engine import nim_complete  # lazy: avoids httpx at import
    except Exception:
        return None
    system, user = build_extraction_prompt(turns, notebook=notebook)
    try:
        raw = await nim_complete(
            system,
            user,
            client,
            temperature=EXTRACTION_TEMPERATURE,
            max_tokens=EXTRACTION_MAX_TOKENS,
        )
    except Exception:
        return None
    if raw is None:
        return None
    parsed = parse_extraction(raw)
    if parsed is None:
        preview = (raw or "")[:200]
        warning("knowledge_extraction: malformed model output; will retry", preview=preview)
        return None
    source_keys = [t.get("id") for t in turns if t.get("id")]
    return candidates_from_parsed(
        parsed, source_keys, anchor_elliot=(notebook == "relational")
    )


def persist_candidates(notebook: str, entities: list[dict], relations: list[dict]) -> tuple[int, int]:
    """Append candidates to the store (origin="she"). Returns (n_entities, n_relations).

    Append-only; reconciliation/locks happen at read time in a later step.
    """
    from memory import knowledge_store as ks

    ne = 0
    for e in entities:
        ks.append_entity(
            notebook,
            id=e["id"],
            type=e["type"],
            name=e["name"],
            aliases=e.get("aliases"),
            origin="she",
        )
        ne += 1
    nr = 0
    for r in relations:
        ks.append_relation(
            notebook,
            id=r["id"],
            subject_id=r["subject_id"],
            predicate=r["predicate"],
            object_value=r["object_value"],
            object_kind=r["object_kind"],
            provenance=r.get("provenance"),
            confidence=r.get("confidence", 0.5),
            origin="she",
        )
        nr += 1
    return ne, nr


# ── self-test (offline; no NIM, no httpx) ───────────────────────────

if __name__ == "__main__":
    import shutil
    import tempfile
    from pathlib import Path
    from memory import knowledge_store as ks

    # 1) parser tolerates code fences + surrounding prose
    fenced = '```json\n{"entities": [], "relations": []}\n```'
    assert parse_extraction(fenced) == {"entities": [], "relations": []}

    # Valid empty (both keys present, both lists)
    assert parse_extraction('{"entities": [], "relations": []}') == {"entities": [], "relations": []}

    # Malformed returns None (not {})
    assert parse_extraction("garbage no json") is None
    assert parse_extraction(None) is None
    assert parse_extraction('') is None
    assert parse_extraction('prefix {"entities": []} suffix') is None  # missing "relations"
    assert parse_extraction('{"entities": []}') is None                # missing "relations"
    assert parse_extraction('{"relations": []}') is None               # missing "entities"
    assert parse_extraction('{"entities": {}, "relations": []}') is None  # entities not a list
    assert parse_extraction('{"entities": [], "relations": {}}') is None  # relations not a list
    assert parse_extraction('[]') is None                                # list, not dict

    # 2) normalizers
    assert _norm_predicate("  Grew Up  In ") == "grew_up_in"
    assert _norm_type("PERSON") == "person"
    assert _norm_type("weather") == "topic"
    assert _clamp_conf("1.7") == 1.0 and _clamp_conf(None) == 0.5 and _clamp_conf(-3) == 0.0

    # 2b) predicate normalization (Task B: controlled vocabulary)
    # -- exact aliases
    assert _normalize_predicate("grew_up") == "grew_up_in"
    assert _normalize_predicate("loves") == "likes"
    assert _normalize_predicate("hates") == "dislikes"
    assert _normalize_predicate("cares_about") == "values"
    assert _normalize_predicate("knows_about") == "knows"
    assert _normalize_predicate("studies") == "studied"
    assert _normalize_predicate("is_good_at") == "skilled_at"
    # -- already canonical (unchanged)
    assert _normalize_predicate("grew_up_in") == "grew_up_in"
    assert _normalize_predicate("likes") == "likes"
    assert _normalize_predicate("knows") == "knows"
    assert _normalize_predicate("studied") == "studied"
    # -- pattern aliases
    assert _normalize_predicate("had_unpleasant_experiences_due_to") == "affected_by", \
        "*_due_to suffix must map to affected_by"
    assert _normalize_predicate("affected_by_circumstances") == "affected_by", \
        "affected_by_* prefix must collapse to affected_by"
    assert _normalize_predicate("had_traumatic_experiences") == "affected_by", \
        "had_*_experiences pattern must map to affected_by"
    assert _normalize_predicate("due_to_poverty") == "affected_by", \
        "due_to_* prefix must map to affected_by"
    # -- length guard: >3 tokens with no alias -> generic related_to
    assert _normalize_predicate("a_very_long_freeform_phrase") == "related_to", \
        ">3 tokens with no alias must map to related_to"
    assert _normalize_predicate("something_related_to_all_things") == "related_to", \
        "5 tokens with no alias must map to related_to"
    assert _normalize_predicate("grew_up_in") == "grew_up_in", \
        "3-token canonical predicate must NOT map to generic"
    assert _normalize_predicate("loves_good_jazz") == "loves_good_jazz", \
        "3-token predicate with no alias must be kept as-is (not >3 tokens)"
    # -- empty/missing input
    assert _normalize_predicate("") == ""
    assert _normalize_predicate(None) == ""

    # 3) candidate shaping from a canned model reply (the L1-miss scenario)
    parsed = {
        "entities": [
            {"name": "Maya", "type": "person", "aliases": []},
            {"name": "Sage", "type": "project", "aliases": ["Sage v2"]},
        ],
        "relations": [
            {"subject": "Elliot", "predicate": "grew up in", "object": "a low-income neighborhood", "object_type": "literal", "confidence": 0.9},
            {"subject": "Elliot", "predicate": "builds", "object": "Sage", "object_type": "entity", "confidence": 1.2},
            {"subject": "Elliot", "predicate": "knows", "object": "Maya", "object_type": "entity", "confidence": 0.8},
            {"subject": "Ghost", "predicate": "haunts", "object": "nothing", "object_type": "literal", "confidence": 0.5},
        ],
    }
    ents, rels = candidates_from_parsed(parsed, ["user_1", "assistant_2"])

    ent_ids = {e["id"] for e in ents}
    assert ELLIOT_ENTITY_ID in ent_ids, "Elliot node must be synthesized when referenced"
    assert "person:maya" in ent_ids and "project:sage" in ent_ids

    # 4 relations in, but the 'Ghost' one is dropped (subject not a listed entity)
    assert len(rels) == 3, f"expected 3 relations, got {len(rels)}"
    by_pred = {r["predicate"]: r for r in rels}
    assert by_pred["grew_up_in"]["object_kind"] == "literal"
    assert by_pred["grew_up_in"]["object_value"] == "a low-income neighborhood"
    assert by_pred["grew_up_in"]["subject_id"] == ELLIOT_ENTITY_ID
    assert by_pred["builds"]["object_kind"] == "entity"
    assert by_pred["builds"]["object_value"] == "project:sage"
    assert by_pred["builds"]["confidence"] == 1.0  # clamped
    assert all(r["provenance"] == ["user_1", "assistant_2"] for r in rels)

    # 3b) durability + quality filters drop transient / fragment / self-referential
    noisy = {
        "entities": [{"name": "Hayumi", "type": "person", "aliases": []}],
        "relations": [
            {"subject": "Elliot", "predicate": "feels", "object": "concerned", "object_type": "literal", "confidence": 0.8},
            {"subject": "Elliot", "predicate": "wants to help", "object": "Hayumi", "object_type": "entity", "confidence": 0.8},
            {"subject": "Elliot", "predicate": "asked about", "object": "socioeconomic", "object_type": "literal", "confidence": 0.7},
            {"subject": "Elliot", "predicate": "attended", "object": "Elliot's old school", "object_type": "literal", "confidence": 0.8},
            {"subject": "Elliot", "predicate": "studied", "object": "music theory", "object_type": "literal", "confidence": 0.3},
            {"subject": "Elliot", "predicate": "knows", "object": "Hayumi", "object_type": "entity", "confidence": 0.9},
        ],
    }
    _, nrels = candidates_from_parsed(noisy, ["user_9"])
    npreds = {r["predicate"] for r in nrels}
    assert "feels" not in npreds, "transient 'feels' must be dropped"
    assert "wants_to_help" not in npreds, "transient intention must be dropped"
    assert "asked_about" not in npreds, "transient 'asked_about' must be dropped"
    assert not any(r["predicate"] == "attended" for r in nrels), "self-referential object must be dropped"
    assert not any(r["predicate"] == "studied" for r in nrels), "below-confidence fact must be dropped"
    assert npreds == {"knows"}, f"only 'knows Hayumi' should remain, got {sorted(npreds)}"

    # 3c) self-referential ENTITY names are refused, not just literal objects.
    # The model sometimes emits "Elliot's old school" as a place entity and links
    # it via attended; the clean place is emitted separately. The garbled
    # possessive node must be refused and its relation dropped, while the clean
    # one survives.
    selfref = {
        "entities": [
            {"name": "Elliot's old school", "type": "place", "aliases": []},
            {"name": "Lincoln High", "type": "place", "aliases": []},
        ],
        "relations": [
            {"subject": "Elliot", "predicate": "attended", "object": "Elliot's old school", "object_type": "entity", "confidence": 0.8},
            {"subject": "Elliot", "predicate": "attended", "object": "Lincoln High", "object_type": "entity", "confidence": 0.8},
        ],
    }
    sents, srels = candidates_from_parsed(selfref, ["user_12"])
    sent_ids = {e["id"] for e in sents}
    assert "place:elliot-s-old-school" not in sent_ids, "possessive self-ref entity must be refused"
    assert "place:lincoln-high" in sent_ids, "clean place entity must survive"
    assert all("elliot-s-old-school" not in str(r["object_value"]) for r in srels), "relation to self-ref entity must be dropped"
    assert any(r["object_value"] == "place:lincoln-high" for r in srels), "clean attended relation must survive"

    # 3d) predicate normalization through full pipeline (Task B): freeform
    # predicates like had_unpleasant_experiences_due_to get canonicalized.
    freeform = {
        "entities": [],
        "relations": [
            {"subject": "Elliot", "predicate": "had_unpleasant_experiences_due_to", "object": "poverty", "object_type": "literal", "confidence": 0.9},
            {"subject": "Elliot", "predicate": "grew up in", "object": "a poor area", "object_type": "literal", "confidence": 0.8},
            {"subject": "Elliot", "predicate": "loves", "object": "music theory", "object_type": "literal", "confidence": 0.7},
            {"subject": "Elliot", "predicate": "a_very_long_freeform_predicate_here", "object": "something vague", "object_type": "literal", "confidence": 0.6},
        ],
    }
    _, frels = candidates_from_parsed(freeform, ["user_15"])
    fpreds = {r["predicate"] for r in frels}
    assert "affected_by" in fpreds, \
        f"had_unpleasant_experiences_due_to must canonicalize to affected_by, got {fpreds}"
    assert "grew_up_in" in fpreds, f"grew up in must normalize to grew_up_in, got {fpreds}"
    assert "likes" in fpreds, f"loves must normalize to likes, got {fpreds}"
    assert "related_to" in fpreds, \
        f"long unmapped predicate must generic-map to related_to, got {fpreds}"
    assert "had_unpleasant_experiences_due_to" not in fpreds, \
        "freeform predicate must NOT survive as-is"
    assert "a_very_long_freeform_predicate_here" not in fpreds, \
        "long unmapped predicate must NOT survive as-is"
    assert len(frels) == 4, f"expected all 4 to survive normalization, got {len(frels)}"

    # 4) persist round-trip into a temp 'interior' notebook, then load back
    tmp = Path(tempfile.mkdtemp())
    ks._NOTEBOOK_PATHS["interior"] = (tmp / "e.jsonl", tmp / "r.jsonl")
    ne, nr = persist_candidates("interior", ents, rels)
    assert (ne, nr) == (len(ents), len(rels))
    loaded_e = ks.load_entities("interior")
    loaded_r = ks.load_relations("interior")
    assert len(loaded_e) == len(ents) and len(loaded_r) == len(rels)
    assert all(e["origin"] == "she" and e["locked"] is False for e in loaded_e)
    assert all(r["origin"] == "she" for r in loaded_r)
    shutil.rmtree(tmp)

    # 5) prompt builder shape
    sys_msg, usr_msg = build_extraction_prompt(
        [{"role": "user", "content": "I grew up poor."}], notebook="relational"
    )
    assert "Elliot" in sys_msg and "STRICT JSON" in sys_msg
    assert "[Elliot] I grew up poor." in usr_msg

    print("OK")
