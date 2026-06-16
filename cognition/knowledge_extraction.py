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

Everything degrades silently: a model failure or unparseable output yields
empty candidate lists, never an exception. nim_complete is imported lazily
inside the async orchestrator so this module stays importable (and testable)
without httpx present.
"""

from __future__ import annotations

import json
import re
from typing import Any

from memory.knowledge_store import make_entity_id, make_relation_id

# Entity types we recognize; anything else collapses to "topic".
VALID_ENTITY_TYPES = ("person", "place", "project", "org", "topic", "event")

# Extraction model settings -- deliberately low temperature for stable,
# factual extraction over a transcript.
EXTRACTION_TEMPERATURE = 0.2
EXTRACTION_MAX_TOKENS = 1024

# How much of each turn to feed the model, and how many turns per batch.
MAX_CHARS_PER_TURN = 600
MAX_TURNS_PER_BATCH = 12

# Canonical anchor for the human user in the relational notebook. First-person
# references and "Elliot" all resolve to this stable id so personal facts
# accrete on one node (the L1 miss this layer exists to fix).
ELLIOT_ENTITY_ID = "person:elliot"
_ELLIOT_ALIASES = frozenset({"elliot", "i", "me", "my", "myself", "user"})


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


# ── prompt ──────────────────────────────────────────────

_SCHEMA_BLOCK = (
    'Return STRICT JSON only -- no prose, no code fences -- in exactly this shape:\n'
    '{\n'
    '  "entities": [{"name": "...", "type": "person|place|project|org|topic|event", "aliases": ["..."]}],\n'
    '  "relations": [{"subject": "...", "predicate": "snake_case_verb", "object": "...", "object_type": "entity|literal", "confidence": 0.0}]\n'
    '}\n'
    'Rules: "type" is one of person/place/project/org/topic/event. "subject" and '
    '"object" are entity names. Use "Elliot" for the human user. Set "object_type" '
    'to "entity" when the object is one of the listed entities, otherwise "literal" '
    'for a free value (a place name, a feeling, a date, a phrase). "predicate" is a '
    'short snake_case phrase (e.g. grew_up_in, works_on, lives_in, feels, knows, '
    'prefers, studied). "confidence" is 0-1 for how strongly the transcript '
    'supports the fact. If nothing durable is present, return '
    '{"entities": [], "relations": []}.'
)

_RELATIONAL_FOCUS = (
    "You read a transcript between Elliot (the human) and Sage (the AI). Extract "
    "DURABLE facts -- things still true tomorrow -- about Elliot and the people, "
    "places, projects, organizations, topics, and events in HIS life. Strongly "
    "prefer facts where Elliot is the subject: personal history, where he grew up, "
    "relationships, work, preferences, beliefs, circumstances. IGNORE anything Sage "
    "says about itself or its own inner life, and ignore transient chit-chat."
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

def parse_extraction(raw: str | None) -> dict:
    """Robustly pull the JSON object out of a model reply. {} on any failure."""
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
        pred = _norm_predicate(r.get("predicate"))
        obj = _as_text(r.get("object"))
        if not subj or not pred or not obj:
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
        referenced_ids.add(subj_id)
        relations_out.append(
            {
                "id": make_relation_id(subj_id, pred, object_value),
                "subject_id": subj_id,
                "predicate": pred,
                "object_value": object_value,
                "object_kind": object_kind,
                "confidence": _clamp_conf(r.get("confidence")),
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

    Returns (entity_records, relation_records). Empty on any failure. Does not
    persist -- call persist_candidates() to write.
    """
    if not turns:
        return [], []
    try:
        from models.inference.engine import nim_complete  # lazy: avoids httpx at import
    except Exception:
        return [], []
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
        return [], []
    parsed = parse_extraction(raw)
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
    assert parse_extraction("garbage no json") == {}
    assert parse_extraction(None) == {}
    assert parse_extraction('prefix {"entities": []} suffix') == {"entities": []}

    # 2) normalizers
    assert _norm_predicate("  Grew Up  In ") == "grew_up_in"
    assert _norm_type("PERSON") == "person"
    assert _norm_type("weather") == "topic"
    assert _clamp_conf("1.7") == 1.0 and _clamp_conf(None) == 0.5 and _clamp_conf(-3) == 0.0

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
