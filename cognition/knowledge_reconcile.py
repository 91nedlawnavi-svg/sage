"""
Knowledge reconcile — Phase 4 Layer 2, step 3.

Collapses the append-only knowledge notebooks into a single *current view*,
honoring sticky-note corrections and locks.

Pure, offline logic — no network, no NIM. Reads via memory.knowledge_store.

Precedence (per record id):
    locked  >  origin == "elliot"  >  origin == "she"
    (ties broken by most recent ts)

Cross-id correction (single-valued predicates only):
    For a given (subject_id, predicate) where the predicate is single-valued,
    only ONE value may be current. The highest-precedence record wins; any
    contradicting machine-derived values are suppressed. This is what makes a
    hand-fixed, locked fact override a stale machine fact even though they
    carry different relation ids (the id hashes the value).
"""

from __future__ import annotations

import re

from memory import knowledge_store


# ── Single-valued predicate registry ────────────────────────────────
# Predicates for which only ONE value may be current per subject. This is the
# tunable knob: extend it as real duplicate/conflict patterns show up.
SINGLE_VALUED_PREDICATES: frozenset[str] = frozenset(
    {
        "lives_in",
        "located_in",
        "based_in",
        "hometown",
        "born_in",
        "birthplace",
        "birthday",
        "birthdate",
        "date_of_birth",
        "age",
        "full_name",
        "name",
        "employer",
        "works_at",
        "job_title",
        "title",
        "role",
        "occupation",
        "nationality",
        "gender",
        "email",
        "email_address",
        "phone",
        "phone_number",
        "timezone",
        "marital_status",
        "spouse",
        "partner",
        "primary_language",
        "native_language",
        "current_project",
    }
)


def _norm_predicate(predicate: str) -> str:
    """Normalize a predicate for matching: lowercase, non-alphanumeric → '_'."""
    return re.sub(r"[^a-z0-9]+", "_", (predicate or "").lower()).strip("_")


def is_single_valued(predicate: str) -> bool:
    """True if *predicate* may hold only one current value per subject."""
    return _norm_predicate(predicate) in SINGLE_VALUED_PREDICATES


# ── Precedence ──────────────────────────────────────────────
def _rank(record: dict) -> int:
    """Authority rank: locked (2) > elliot (1) > she/other (0)."""
    if record.get("locked"):
        return 2
    if record.get("origin") == "elliot":
        return 1
    return 0


def _ts(record: dict) -> str:
    """Sort key for recency. ISO strings compare correctly lexically."""
    return record.get("ts") or record.get("last_seen") or record.get("first_seen") or ""


def _pick_winner(records: list[dict]) -> dict:
    """Return the highest-precedence record, ties broken by most recent ts."""
    best = records[0]
    best_key = (_rank(best), _ts(best))
    for rec in records[1:]:
        key = (_rank(rec), _ts(rec))
        if key > best_key:
            best, best_key = rec, key
    return best


# ── Merge helpers (enrich the winner; never lose provenance) ────────
def _dedupe(seq) -> list:
    out: list = []
    for item in seq:
        if item not in out:
            out.append(item)
    return out


def _merge_seen(records: list[dict]) -> tuple[str, str]:
    firsts = [r.get("first_seen") or r.get("ts") for r in records if (r.get("first_seen") or r.get("ts"))]
    lasts = [r.get("last_seen") or r.get("ts") for r in records if (r.get("last_seen") or r.get("ts"))]
    first_seen = min(firsts) if firsts else ""
    last_seen = max(lasts) if lasts else ""
    return first_seen, last_seen


def _collapse_by_id(records: list[dict], *, kind: str) -> dict[str, dict]:
    """Group records by id; collapse each group to one enriched winner."""
    groups: dict[str, list[dict]] = {}
    for rec in records:
        rid = rec.get("id")
        if not rid:
            continue
        groups.setdefault(rid, []).append(rec)

    collapsed: dict[str, dict] = {}
    for rid, group in groups.items():
        winner = dict(_pick_winner(group))
        first_seen, last_seen = _merge_seen(group)
        if first_seen:
            winner["first_seen"] = first_seen
        if last_seen:
            winner["last_seen"] = last_seen
        if kind == "entity":
            aliases: list = []
            for r in group:
                aliases.extend(r.get("aliases") or [])
            winner["aliases"] = _dedupe(aliases)
        elif kind == "relation":
            prov: list = []
            for r in group:
                prov.extend(r.get("provenance") or [])
            winner["provenance"] = _dedupe(prov)
        collapsed[rid] = winner
    return collapsed


# ── Public reconcile ──────────────────────────────────────
def reconcile_entities(records: list[dict]) -> dict[str, dict]:
    """Collapse entity records to the current view, keyed by entity id."""
    return _collapse_by_id(records, kind="entity")


def reconcile_relations(records: list[dict]) -> dict[str, dict]:
    """
    Collapse relation records to the current view, keyed by relation id.

    After per-id collapse, single-valued predicates are de-conflicted across
    ids: for each (subject_id, predicate) that is single-valued, only the
    highest-precedence record survives; contradicting values are suppressed.
    """
    by_id = _collapse_by_id(records, kind="relation")

    sv_groups: dict[tuple[str, str], list[str]] = {}
    survivors: dict[str, dict] = {}
    for rid, rec in by_id.items():
        subject = rec.get("subject_id") or ""
        predicate = rec.get("predicate") or ""
        if subject and is_single_valued(predicate):
            key = (subject, _norm_predicate(predicate))
            sv_groups.setdefault(key, []).append(rid)
        else:
            survivors[rid] = rec  # multi-valued / unkeyed: always kept

    for _key, rids in sv_groups.items():
        contenders = [by_id[r] for r in rids]
        winner = _pick_winner(contenders)
        survivors[winner["id"]] = winner

    return survivors


def reconcile_notebook(notebook: str) -> dict:
    """
    Load *notebook* from the store and return its reconciled current view:

        {"entities": {id: entity, ...}, "relations": {id: relation, ...}}

    Safe: returns empty views if the notebook is unknown or empty.
    """
    entities = reconcile_entities(knowledge_store.load_entities(notebook))
    relations = reconcile_relations(knowledge_store.load_relations(notebook))
    return {"entities": entities, "relations": relations}


# ── Self-test ───────────────────────────────────────────
if __name__ == "__main__":
    import shutil
    import tempfile
    from pathlib import Path

    # ---- unit: reconcile_entities ----
    ents = [
        {"id": "person:elliot", "kind": "entity", "type": "person", "name": "Elliot",
         "aliases": ["El"], "origin": "she", "locked": False, "ts": "2026-01-01T00:00:00"},
        {"id": "person:elliot", "kind": "entity", "type": "person", "name": "Elliot",
         "aliases": ["E"], "origin": "she", "locked": False, "ts": "2026-01-02T00:00:00",
         "first_seen": "2026-01-02T00:00:00", "last_seen": "2026-01-02T00:00:00"},
        # origin beats recency:
        {"id": "person:sage", "kind": "entity", "type": "person", "name": "SAGE-bot",
         "aliases": [], "origin": "she", "locked": False, "ts": "2026-01-09T00:00:00"},
        {"id": "person:sage", "kind": "entity", "type": "person", "name": "Sage",
         "aliases": ["She"], "origin": "elliot", "locked": False, "ts": "2026-01-01T00:00:00"},
        # locked beats recency:
        {"id": "concept:home", "kind": "entity", "type": "concept", "name": "WRONG",
         "origin": "she", "locked": False, "ts": "2026-02-09T00:00:00"},
        {"id": "concept:home", "kind": "entity", "type": "concept", "name": "Home",
         "origin": "she", "locked": True, "ts": "2026-02-01T00:00:00"},
    ]
    re_ents = reconcile_entities(ents)
    assert set(re_ents) == {"person:elliot", "person:sage", "concept:home"}
    assert re_ents["person:elliot"]["ts"] == "2026-01-02T00:00:00"
    assert re_ents["person:elliot"]["aliases"] == ["El", "E"], re_ents["person:elliot"]["aliases"]
    assert re_ents["person:elliot"]["first_seen"] == "2026-01-01T00:00:00"
    assert re_ents["person:elliot"]["last_seen"] == "2026-01-02T00:00:00"
    assert re_ents["person:sage"]["name"] == "Sage"          # elliot beat later she
    assert re_ents["concept:home"]["name"] == "Home"          # locked beat later she
    assert re_ents["concept:home"]["locked"] is True

    # ---- unit: reconcile_relations, per-id provenance union ----
    kid = knowledge_store.make_relation_id("person:elliot", "knows", "concept:python")
    rels_prov = [
        {"id": kid, "kind": "relation", "subject_id": "person:elliot", "predicate": "knows",
         "object": {"kind": "entity", "value": "concept:python"}, "provenance": ["user_1"],
         "confidence": 0.5, "origin": "she", "locked": False, "ts": "2026-01-01T00:00:00"},
        {"id": kid, "kind": "relation", "subject_id": "person:elliot", "predicate": "knows",
         "object": {"kind": "entity", "value": "concept:python"}, "provenance": ["user_2"],
         "confidence": 0.9, "origin": "she", "locked": False, "ts": "2026-01-05T00:00:00"},
    ]
    re_prov = reconcile_relations(rels_prov)
    assert list(re_prov) == [kid]
    assert re_prov[kid]["provenance"] == ["user_1", "user_2"], re_prov[kid]["provenance"]
    assert re_prov[kid]["confidence"] == 0.9                  # latest she

    # ---- unit: single-valued suppression + felt-test #2 ----
    def rel(subj, pred, val, origin, ts, locked=False):
        rid = knowledge_store.make_relation_id(subj, pred, val)
        return {"id": rid, "kind": "relation", "subject_id": subj, "predicate": pred,
                "object": {"kind": "literal", "value": val}, "provenance": [], "confidence": 0.7,
                "origin": origin, "locked": locked, "ts": ts}

    paris = rel("person:elliot", "lives_in", "Paris", "she", "2026-01-01T00:00:00")
    jakarta = rel("person:elliot", "lives_in", "Jakarta", "elliot", "2026-01-02T00:00:00", locked=True)
    london_later = rel("person:elliot", "lives_in", "London", "she", "2026-03-01T00:00:00")
    re_loc = reconcile_relations([paris, jakarta, london_later])
    assert len(re_loc) == 1, f"expected 1 surviving lives_in, got {len(re_loc)}"
    survivor = next(iter(re_loc.values()))
    assert survivor["object"]["value"] == "Jakarta", survivor   # locked correction wins over later machine fact

    # ---- unit: single-valued, machine-only → latest wins ----
    acme = rel("person:elliot", "employer", "Acme", "she", "2026-01-01T00:00:00")
    globex = rel("person:elliot", "employer", "Globex", "she", "2026-02-01T00:00:00")
    re_emp = reconcile_relations([acme, globex])
    assert len(re_emp) == 1
    assert next(iter(re_emp.values()))["object"]["value"] == "Globex"

    # ---- unit: multi-valued predicate keeps all ----
    pizza = rel("person:elliot", "likes", "pizza", "she", "2026-01-01T00:00:00")
    ramen = rel("person:elliot", "likes", "ramen", "she", "2026-01-02T00:00:00")
    re_likes = reconcile_relations([pizza, ramen])
    assert len(re_likes) == 2, f"expected both likes kept, got {len(re_likes)}"

    # ---- integration: reconcile_notebook via tempdir ----
    tmp = Path(tempfile.mkdtemp())
    knowledge_store._NOTEBOOK_PATHS["interior"] = (
        tmp / "interior_entities.jsonl",
        tmp / "interior_relations.jsonl",
    )
    e_id = knowledge_store.make_entity_id("person", "Elliot")
    knowledge_store.append_entity("interior", id=e_id, type="person", name="Elliot", origin="she")
    p_id = knowledge_store.make_relation_id("person:elliot", "lives_in", "Paris")
    j_id = knowledge_store.make_relation_id("person:elliot", "lives_in", "Jakarta")
    knowledge_store.append_relation("interior", id=p_id, subject_id="person:elliot",
        predicate="lives_in", object_value="Paris", object_kind="literal", origin="she", confidence=0.6)
    knowledge_store.append_relation("interior", id=j_id, subject_id="person:elliot",
        predicate="lives_in", object_value="Jakarta", object_kind="literal", origin="elliot",
        locked=True, confidence=1.0)
    view = reconcile_notebook("interior")
    assert e_id in view["entities"]
    assert len(view["relations"]) == 1, view["relations"]
    assert next(iter(view["relations"].values()))["object"]["value"] == "Jakarta"
    shutil.rmtree(tmp)

    print("OK")
