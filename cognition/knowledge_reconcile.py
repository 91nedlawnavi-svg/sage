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

Literal grounding (read-time, see _ground_literals):
    The extractor sometimes emits an object as a free-text *literal* that really
    names an entity (a school, a town, a person). Such a fact never links into
    the graph, and two phrasings of the same thing ("old school" vs "Elliot's
    old school") never converge. Before collapsing, reconcile rewrites literals
    on entity-bearing predicates into entity refs — deterministically, and
    only in the view. The raw append-only store is never mutated, exactly like
    dedup and locks.
"""

from __future__ import annotations

import re

from memory import knowledge_store


# ── Single-valued predicate registry ────────────────────────────
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
    "is_interested_in": "interested_in",
    "cares_about": "values",
    "enjoys": "likes",
    "loves": "likes",
    "hates": "dislikes",
    "thinks": "believes",
    "studies": "studied",
    "has_role_as": "has_role",
    "prefers_to_be_seen_as": "prefers_to_be_seen_as",
}

_GENERIC_CATCHALL = "related_to"
_MAX_PREDICATE_TOKENS = 3


def _alias_by_pattern(predicate: str) -> str | None:
    if predicate.endswith("_due_to"):
        return "affected_by"
    if predicate.startswith("due_to_"):
        return "affected_by"
    if predicate.startswith("affected_by_"):
        return "affected_by"
    if predicate.startswith("had_") and predicate.endswith("_experiences"):
        return "affected_by"
    return None


def _canonical_predicate(predicate: str) -> str:
    """View-only predicate canonicalization for old and new records."""
    pred = _norm_predicate(predicate)
    if not pred:
        return pred
    canonical = _PREDICATE_ALIASES.get(pred)
    if canonical is None:
        canonical = _alias_by_pattern(pred)
    if canonical:
        return canonical
    if len(pred.split("_")) > _MAX_PREDICATE_TOKENS:
        return _GENERIC_CATCHALL
    return pred


def _normalize_literal_value(raw: str) -> str:
    """Normalize a literal object value for deterministic dedup id computation.

    Lowercase, strip surrounding punctuation, collapse internal whitespace,
    then strip leading possessive/article tokens (same set as ``_ground_name``).
    Returns ``""`` when nothing usable remains.

    This is a *view-only* normalizer — the store is never mutated.  It exists so
    that ``Ramen`` and ``ramen.`` produce the same normalised id and collapse
    under the existing ``_collapse_by_id`` pass.
    """
    text = (raw or "").strip().lower().strip(".,;:!?\"'""''")
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)  # collapse internal whitespace runs
    tokens = text.split()
    i = 0
    while i < len(tokens) and tokens[i].strip(".,;:!?\"'""''") in _LEADING_DROP_TOKENS:
        i += 1
    result = " ".join(tokens[i:]).strip()
    return result


def _normalize_for_dedup(relations: list[dict]) -> list[dict]:
    """View-only pass: normalize predicates and literal object values so
    surface-form variants of the same (subject, predicate, literal-object)
    produce the same relation id and collapse under ``_collapse_by_id``.

    * Predicate — lowered via ``_norm_predicate`` (so ``grew up in`` /
      ``grew_up_in`` converge).
    * Literal object value — lowered via ``_normalize_literal_value`` (so
      ``Ramen``, ``ramen.``, and ``my ramen`` converge).
    * Entity object values are untouched (they already have stable ids).

    Returns **new** records (shallow copies) when anything changed; original
    records are returned as-is so idempotent paths cost nothing.

    Hard invariants:
    * Never merges across different subjects (id hashes subject_id).
    * Does not disturb single-valued de-confliction (which runs later, over
      the already-collapsed set).
    * No embedding-based semantic merging — deferred.
    """
    out: list[dict] = []
    for rec in relations:
        rid = rec.get("id")
        if not rid:
            out.append(rec)
            continue

        subj = rec.get("subject_id") or ""
        pred = _canonical_predicate(rec.get("predicate", ""))
        obj = rec.get("object") or {}

        if obj.get("kind") == "literal":
            raw_val = str(obj.get("value", ""))
            norm_val = _normalize_literal_value(raw_val)
            if not norm_val:
                out.append(rec)
                continue
            new_id = knowledge_store.make_relation_id(subj, pred, norm_val)
            # Use the normalized form ONLY for id computation so surface-form
            # duplicates collapse.  The object value keeps its original casing
            # for display — _pick_winner preserves the winner's original text.
            if new_id != rid:
                new_rec = dict(rec)
                new_rec["predicate"] = pred
                new_rec["id"] = new_id
                out.append(new_rec)
            elif pred != rec.get("predicate", ""):
                # Id unchanged (value already normalized for id) but predicate
                # differs in its raw form (e.g. "grew up in" vs "grew_up_in"
                # that happened to produce the same id — unlikely but safe).
                new_rec = dict(rec)
                new_rec["predicate"] = pred
                out.append(new_rec)
            else:
                out.append(rec)
        else:
            # Entity object: only predicate normalization applies
            ent_val = str(obj.get("value", ""))
            new_id = knowledge_store.make_relation_id(subj, pred, ent_val)
            if new_id != rid or pred != rec.get("predicate", ""):
                new_rec = dict(rec)
                new_rec["predicate"] = pred
                new_rec["id"] = new_id
                out.append(new_rec)
            else:
                out.append(rec)

    return out


def is_single_valued(predicate: str) -> bool:
    """True if *predicate* may hold only one current value per subject."""
    return _norm_predicate(predicate) in SINGLE_VALUED_PREDICATES


# ── Literal grounding ──────────────────────────────────────
# When the object of a relation is a literal but its predicate clearly points at
# an entity, promote the literal into an entity ref so the fact joins the graph
# and repeated phrasings converge on one node. The target type is inferred
# deterministically from the predicate; everything not in this map stays a
# literal. This is conservative on purpose — grow the map from felt need.
_GROUNDING_PREDICATE_TYPES: dict[str, str] = {
    # → place
    "grew_up_in": "place",
    "born_in": "place",
    "birthplace": "place",
    "lives_in": "place",
    "located_in": "place",
    "based_in": "place",
    "hometown": "place",
    "from": "place",
    "moved_to": "place",
    "visited": "place",
    # → org (schools, companies, institutions)
    "studied_at": "org",
    "attended": "org",
    "graduated_from": "org",
    "works_at": "org",
    "employed_by": "org",
    "employer": "org",
    "member_of": "org",
    # → person
    "knows": "person",
    "friend_of": "person",
    "sibling_of": "person",
    "parent_of": "person",
    "child_of": "person",
    "married_to": "person",
    "spouse": "person",
    "partner": "person",
    "colleague_of": "person",
    "mentored_by": "person",
    # Phase 5 Brick 4.5 — view-layer grounding refinements
    "built_by": "person",
}

# Leading possessive / article tokens stripped before a literal is turned into
# an entity name, so "Elliot's old school", "the old school", and "old school"
# all converge on the same node.
_LEADING_DROP_TOKENS: frozenset[str] = frozenset(
    {
        "elliot's", "elliots", "elliot's",
        "his", "her", "their", "my", "our", "your", "its",
        "the", "a", "an",
    }
)


def _slug(text: str) -> str:
    """Mirror knowledge_store's slug rule (kept local to avoid coupling)."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _ground_name(raw: str) -> str:
    """Normalize a literal object into a clean entity *name*.

    Strips a leading possessive/article run ("Elliot's old school" → "old
    school") and surrounding punctuation/whitespace. Returns "" when nothing
    usable remains (e.g. the literal was only articles).
    """
    text = (raw or "").strip().strip(".,;:!?\"'""''")
    if not text:
        return ""
    tokens = text.split()
    i = 0
    while i < len(tokens) and tokens[i].lower().strip(".,;:!?\"'""''") in _LEADING_DROP_TOKENS:
        i += 1
    name = " ".join(tokens[i:]).strip()
    return name


def _is_groundable_literal(value: str) -> bool:
    """Return True if a literal value should be grounded into an entity.

    Heuristic: if the literal, after possessive/article stripping, has more
    than 3 space-separated tokens and every token is fully lowercase (no
    proper-noun capitalization), treat it as an abstract descriptive phrase
    rather than a named entity.  Such phrases describe circumstances, not
    places or orgs, and minting a node for them pollutes the graph.

    Examples:
      "socioeconomically challenging circumstances" -> False (abstract)
      "old-school"                                  -> True  (short)
      "old school"                                  -> True  (short)
      "Lincoln High"                                -> True  (proper noun)
    """
    cleaned = _ground_name(value)
    if not cleaned:
        return False
    tokens = cleaned.split()
    if len(tokens) >= 3 and all(t == t.lower() for t in tokens):
        return False
    return True


def _ground_literals(
    entities: list[dict], relations: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Rewrite entity-bearing literal objects into entity refs (view-only).

    Returns ``(entities + any minted entities, grounded_relations)``. Operates
    on copies — the input records and the on-disk store are never mutated. The
    grounded relation's id is recomputed over the new object so grounded
    duplicates collapse together in the id pass that follows.
    """
    # Index existing entities by id, slug(name), and slug(alias).
    by_slug: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    for ent in entities:
        if not ent.get("id"):
            continue
        by_id[ent["id"]] = ent
        nm = ent.get("name") or ""
        if nm:
            by_slug.setdefault(_slug(nm), ent)
        for al in ent.get("aliases") or []:
            if al:
                by_slug.setdefault(_slug(al), ent)

    minted: dict[str, dict] = {}
    grounded: list[dict] = []

    for rec in relations:
        obj = rec.get("object") or {}
        pred = _norm_predicate(rec.get("predicate", ""))
        etype = _GROUNDING_PREDICATE_TYPES.get(pred)

        # Only ground literals on a known entity-bearing predicate.
        if etype is None or obj.get("kind") != "literal":
            grounded.append(rec)
            continue

        # Abstract-literal guard: skip descriptive phrases that are not
        # proper entity names (e.g. "socioeconomically challenging
        # circumstances" -> stays as literal fact, no place: minted).
        if not _is_groundable_literal(str(obj.get("value", ""))):
            grounded.append(rec)
            continue

        name = _ground_name(str(obj.get("value", "")))
        slug = _slug(name)
        if not name or not slug:
            grounded.append(rec)
            continue

        match = by_slug.get(slug)
        if match is not None:
            target_id = match["id"]
        else:
            # Entity-id match: the literal value may itself be an existing
            # entity id (e.g. built_by -> "person:elliot"). Check by_id.
            if ":" in name and name in by_id:
                target_id = name
            else:
                target_id = knowledge_store.make_entity_id(etype, name)
            if target_id not in minted:
                ts = rec.get("ts") or rec.get("last_seen") or rec.get("first_seen") or ""
                minted[target_id] = {
                    "id": target_id,
                    "kind": "entity",
                    "type": etype,
                    "name": name,
                    "aliases": [],
                    "origin": "she",
                    "locked": False,
                    "first_seen": rec.get("first_seen") or ts,
                    "last_seen": rec.get("last_seen") or ts,
                    "ts": ts,
                    "grounded": True,  # provenance marker: minted by grounding
                }
                by_slug[slug] = minted[target_id]
                by_id[target_id] = minted[target_id]

        new_rec = dict(rec)
        new_rec["object"] = {"kind": "entity", "value": target_id}
        new_rec["id"] = knowledge_store.make_relation_id(
            rec.get("subject_id", ""), rec.get("predicate", ""), target_id
        )
        grounded.append(new_rec)

    return entities + list(minted.values()), grounded


# ── Precedence ─────────────────────────────
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


# ── Merge helpers (enrich the winner; never lose provenance) ────
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


# ── Public reconcile ─────────────────────────
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

    Literals on entity-bearing predicates are grounded into entity refs before
    collapse, so repeated phrasings converge and facts join the graph. Safe:
    returns empty views if the notebook is unknown or empty.
    """
    raw_entities = knowledge_store.load_entities(notebook)
    raw_relations = knowledge_store.load_relations(notebook)
    grounded_entities, grounded_relations = _ground_literals(raw_entities, raw_relations)
    # View-only normalization pass: surface-form variants of the same
    # (subject, predicate, literal-object) produce the same id and collapse.
    normalized_relations = _normalize_for_dedup(grounded_relations)
    entities = reconcile_entities(grounded_entities)
    relations = reconcile_relations(normalized_relations)
    return {"entities": entities, "relations": relations}


# ── Self-test ──────────────────────────
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

    # ---- unit: literal grounding (the L2 "old school" drift) ----
    g_entities = [
        {"id": "person:elliot", "kind": "entity", "type": "person", "name": "Elliot",
         "origin": "she", "locked": False, "ts": "2026-01-01T00:00:00"},
        {"id": "person:hayumi", "kind": "entity", "type": "person", "name": "Hayumi",
         "origin": "she", "locked": False, "ts": "2026-01-01T00:00:00"},
    ]
    g_relations = [
        {"id": "x1", "kind": "relation", "subject_id": "person:elliot", "predicate": "studied_at",
         "object": {"kind": "literal", "value": "old school"}, "provenance": ["u1"],
         "confidence": 0.8, "origin": "she", "locked": False, "ts": "2026-01-01T00:00:00"},
        {"id": "x2", "kind": "relation", "subject_id": "person:hayumi", "predicate": "attended",
         "object": {"kind": "literal", "value": "Elliot's old school"}, "provenance": ["u2"],
         "confidence": 0.8, "origin": "she", "locked": False, "ts": "2026-01-02T00:00:00"},
        {"id": "x3", "kind": "relation", "subject_id": "person:elliot", "predicate": "values",
         "object": {"kind": "literal", "value": "exploring ideas"}, "provenance": ["u3"],
         "confidence": 0.7, "origin": "she", "locked": False, "ts": "2026-01-03T00:00:00"},
    ]
    gent, grel = _ground_literals(g_entities, g_relations)
    gent_ids = {e["id"] for e in gent}
    assert "org:old-school" in gent_ids, gent_ids
    school_refs = [r for r in grel if (r["object"].get("value") == "org:old-school")]
    assert len(school_refs) == 2, school_refs                    # both phrasings grounded
    assert all(r["object"]["kind"] == "entity" for r in school_refs)
    assert {r["subject_id"] for r in school_refs} == {"person:elliot", "person:hayumi"}
    vals = [r for r in grel if r["predicate"] == "values"]       # non-grounding predicate untouched
    assert vals and vals[0]["object"]["kind"] == "literal", vals

    # ---- unit: grounding matches an EXISTING entity instead of minting a dup ----
    g_entities2 = g_entities + [
        {"id": "org:lincoln-high", "kind": "entity", "type": "org", "name": "Lincoln High",
         "aliases": ["Lincoln"], "origin": "she", "locked": False, "ts": "2026-01-01T00:00:00"},
    ]
    g_relations2 = [
        {"id": "y1", "kind": "relation", "subject_id": "person:elliot", "predicate": "studied_at",
         "object": {"kind": "literal", "value": "lincoln high"}, "provenance": ["u4"],
         "confidence": 0.8, "origin": "she", "locked": False, "ts": "2026-01-01T00:00:00"},
    ]
    gent2, grel2 = _ground_literals(g_entities2, g_relations2)
    assert grel2[0]["object"] == {"kind": "entity", "value": "org:lincoln-high"}, grel2[0]
    assert sum(1 for e in gent2 if e["id"] == "org:lincoln-high") == 1   # no dup minted

    # ---- unit: _ground_name strips possessives/articles ----
    assert _ground_name("Elliot's old school") == "old school"
    assert _ground_name("the old school") == "old school"
    assert _ground_name("old school") == "old school"
    assert _ground_name("   the   ") == ""

    # ---- integration: reconcile_notebook via tempdir (now grounds lives_in) ----
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
    # lives_in literals are grounded into place entities; locked Jakarta still wins
    assert "place:jakarta" in view["entities"], list(view["entities"].keys())
    assert len(view["relations"]) == 1, view["relations"]
    surv = next(iter(view["relations"].values()))
    assert surv["object"] == {"kind": "entity", "value": "place:jakarta"}, surv
    shutil.rmtree(tmp)

    # ---- unit: deterministic dedup / literal normalization (Task C) ----
    # Ramen / ramen. collapse via _normalize_literal_value -> same id merged
    def rlike(subj, pred, val, ts="2026-01-01T00:00:00", origin="she"):
        rid = knowledge_store.make_relation_id(subj, pred, val)
        return {"id": rid, "kind": "relation", "subject_id": subj, "predicate": pred,
                "object": {"kind": "literal", "value": val}, "provenance": [ts],
                "confidence": 0.7, "origin": origin, "locked": False, "ts": ts}

    # -- two surface-form variants of the same fact collapse to one
    r1 = rlike("person:elliot", "likes", "Ramen", ts="t1")
    r2 = rlike("person:elliot", "likes", "ramen.", ts="t2")
    # Use reconcile_notebook (not reconcile_relations directly) so the
    # normalization pass runs: all "likes" relations stay literal through
    # grounding (non-entity-bearing predicate), then get normalized.
    view = {"entities": {}, "relations": reconcile_relations(
        _normalize_for_dedup([r1, r2])
    )}
    assert len(view["relations"]) == 1, \
        f"Ramen/ramen. should collapse to 1, got {len(view['relations'])}"
    surv = next(iter(view["relations"].values()))
    # Normalized form used ONLY for id computation -- display keeps the
    # winner's original casing (here "ramen." wins on ts "t2" > "t1").
    assert surv["object"]["value"] in ("Ramen", "ramen."), \
        f"expected original casing preserved, got '{surv['object']['value']}'"
    assert surv["provenance"] == ["t1", "t2"], surv  # provenance union
    assert surv["predicate"] == "likes"

    # -- proper-noun casing preserved for non-grounded literal
    r_hi = rlike("person:elliot", "likes", "Classical Music")
    r_lo = rlike("person:elliot", "likes", "classical music")
    view_proper = {"entities": {}, "relations": reconcile_relations(
        _normalize_for_dedup([r_hi, r_lo])
    )}
    assert len(view_proper["relations"]) == 1
    surv = next(iter(view_proper["relations"].values()))
    assert surv["object"]["value"] in ("Classical Music", "classical music"), \
        f"expected original casing preserved, got '{surv['object']['value']}'"

    # -- distinct literals stay separate
    r3 = rlike("person:elliot", "likes", "ramen")
    r4 = rlike("person:elliot", "likes", "sushi")
    view2 = {"entities": {}, "relations": reconcile_relations(
        _normalize_for_dedup([r3, r4])
    )}
    assert len(view2["relations"]) == 2, \
        f"distinct literals must stay separate, got {len(view2['relations'])}"

    # -- cross-subject never merges
    r5 = rlike("person:elliot", "likes", "Ramen")
    r6 = rlike("person:hayumi", "likes", "ramen.")
    view3 = {"entities": {}, "relations": reconcile_relations(
        _normalize_for_dedup([r5, r6])
    )}
    assert len(view3["relations"]) == 2, \
        f"cross-subject must not merge, got {len(view3['relations'])}"

    # -- predicate normalization: grew up in / grew_up_in converge
    r7 = rlike("person:elliot", "grew up in", "a poor area")
    r8 = rlike("person:elliot", "grew_up_in", "a poor area")
    view4 = {"entities": {}, "relations": reconcile_relations(
        _normalize_for_dedup([r7, r8])
    )}
    assert len(view4["relations"]) == 1, \
        f"grew up in / grew_up_in should collapse, got {len(view4['relations'])}"

    print("OK")
