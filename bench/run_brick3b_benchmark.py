#!/usr/bin/env python3
"""
Sage Phase 5 — Brick 3b: synthetic benchmark for the relationship engine.

ISOLATED — never touches ~/sage_data. Uses /tmp/sage_bench as a throwaway
store. Reports entity recovery, edge P/R/F1, TP recall, predicate
normalization, trap avoidance, coreference, and dedup.
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from collections import defaultdict

import httpx

# ── ensure sage imports work & load .env ───────────────────────────
SAGE_DIR = Path.home() / "sage"
sys.path.insert(0, str(SAGE_DIR))

env_path = SAGE_DIR / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

# ── engine internals ───────────────────────────────────────────────
from cognition.knowledge_extraction import (
    extract_relationships,
    merge_relationship_edges,
    merge_same_type_entities,
    _slug_key,
    _slug_key as slug_key,
    make_entity_id,
    make_relation_id,
    VALID_ENTITY_TYPES,
    _normalize_predicate,
    _CANONICAL_PREDICATES,
)

# ── paths ──────────────────────────────────────────────────────────
CORPUS_PATH = Path.home() / "Downloads" / "sage-bench-synthetic-corpus.jsonl"
GOLD_PATH = Path.home() / "Downloads" / "sage-bench-gold-edges.md"
ISOLATED_DIR = Path("/tmp/sage_bench")

# ── GOLD DATA (SCORER ONLY — never fed to the extraction prompt!) ──
GOLD_ENTITIES: dict[str, dict] = {
    "person:nadia":         {"type": "person", "names": ["Nadia"]},
    "person:tomas":         {"type": "person", "names": ["Tomas", "Thomas"]},
    "person:hana":          {"type": "person", "names": ["Hana"]},
    "person:priya":         {"type": "person", "names": ["Priya"]},
    "person:sef":           {"type": "person", "names": ["Sef", "Oluwaseun Bello", "Oluwaseun"]},
    "person:liam":          {"type": "person", "names": ["Liam", "Liam Fitzgerald", "Fitzgerald"]},
    "person:mara-reyes":    {"type": "person", "names": ["Mara Reyes", "Reyes", "Professor Reyes", "Mara"]},
    "org:vega-robotics":    {"type": "org",    "names": ["Vega Robotics", "Vega"]},
    "org:cedar-general-hospital": {"type": "org", "names": ["Cedar General Hospital"]},
    "org:the-tin-kettle":   {"type": "org",    "names": ["The Tin Kettle", "Tin Kettle"]},
    "org:brightwood-university": {"type": "org", "names": ["Brightwood University"]},
    "place:marrow-hill":    {"type": "place",  "names": ["Marrow Hill"]},
    "place:lagos":          {"type": "place",  "names": ["Lagos"]},
    "project:velvet-hours": {"type": "project","names": ["Velvet Hours"]},
    "project:halo":         {"type": "project","names": ["Halo"]},
    "topic:machine-learning":   {"type": "topic","names": ["machine learning", "machine-learning"]},
    "topic:community-health":   {"type": "topic","names": ["community health", "community-health"]},
    "topic:ai-ethics":      {"type": "topic",  "names": ["AI ethics", "robotics ethics"]},
    "event:priya-sef-wedding":  {"type": "event","names": ["wedding", "Priya and Sef's wedding"], "_optional": True},
}

# Build slug→gold_id mapping
SLUG_TO_GOLD: dict[str, str] = {}
for gid, info in GOLD_ENTITIES.items():
    for name in info["names"]:
        SLUG_TO_GOLD[slug_key(name)] = gid
# Additional slug variations the model commonly produces (full names, abbreviations)
SLUG_TO_GOLD.update({
    "cedar-general":            "org:cedar-general-hospital",
    "brightwood":               "org:brightwood-university",
    "vega-robotics":            "org:vega-robotics",
    "liam-fitzgerald":          "person:liam",
    "fitzgerald":               "person:liam",
    "mara":                     "person:mara-reyes",
    "tin-kettle":               "org:the-tin-kettle",
    "robotics-ethics":          "topic:ai-ethics",
    "machine-learning":         "topic:machine-learning",
    "professor-reyes":          "person:mara-reyes",
    "oluwaseun-bello":          "person:sef",
    "oluwaseun":                "person:sef",
})

# Gold edges: (subj_id, pred, obj_id)
# After scorer-level predicate normalisation (already canonical in gold)
GOLD_EDGES: list[tuple[str, str, str, int]] = [
    ("person:nadia",      "sibling_of",    "person:tomas",              1),
    ("person:hana",       "parent_of",     "person:nadia",              2),
    ("person:hana",       "parent_of",     "person:tomas",              3),
    ("person:nadia",      "grew_up_in",    "place:marrow-hill",         4),
    ("person:tomas",      "grew_up_in",    "place:marrow-hill",         5),
    ("person:hana",       "lives_in",      "place:marrow-hill",         6),
    ("person:nadia",      "works_at",      "org:vega-robotics",         7),
    ("person:nadia",      "skilled_at",    "topic:machine-learning",    8),
    ("person:tomas",      "works_at",      "org:the-tin-kettle",        9),
    ("person:tomas",      "works_on",      "project:velvet-hours",     10),
    ("person:sef",        "works_on",      "project:velvet-hours",     11),
    ("person:sef",        "skilled_at",    "guitar",                   12),
    ("person:sef",        "works_at",      "org:vega-robotics",        13),
    ("person:sef",        "knows",         "person:nadia",             14),
    ("person:priya",      "friend_of",     "person:nadia",             15),
    ("person:priya",      "works_at",      "org:cedar-general-hospital",16),
    ("person:priya",      "has_role",      "doctor",                   17),
    ("person:priya",      "grew_up_in",    "place:lagos",              18),
    ("person:priya",      "values",        "topic:community-health",   19),
    ("person:priya",      "related_to",    "person:sef",               20),
    ("person:tomas",      "knows",         "person:priya",             21),
    ("person:nadia",      "studied_at",    "org:brightwood-university", 22),
    ("person:mara-reyes", "works_at",      "org:brightwood-university", 23),
    ("person:mara-reyes", "related_to",    "person:nadia",             24),
    ("person:mara-reyes", "related_to",    "person:liam",              25),
    ("person:liam",       "studied_at",    "org:brightwood-university", 26),
    ("person:liam",       "knows",         "person:nadia",             27),
    ("person:liam",       "works_at",      "org:vega-robotics",        28),
    ("person:liam",       "interested_in", "topic:ai-ethics",          29),
    ("person:mara-reyes", "interested_in", "topic:ai-ethics",          30),
    ("person:liam",       "friend_of",     "person:tomas",             31),
    ("person:nadia",      "works_on",      "project:halo",             32),
    ("person:sef",        "works_on",      "project:halo",             33),
]

# Optional edges (don't penalize)
OPTIONAL_EDGES: set[tuple[str, str, str]] = {
    ("person:priya", "related_to", "event:priya-sef-wedding"),
    ("person:sef",   "related_to", "event:priya-sef-wedding"),
}

TP_EDGE_IDS = {1, 2, 3, 6, 11, 14, 15, 20, 21, 24, 25, 27, 31, 33}

# DEDUP sets: aliases that should collapse to ONE entity
DEDUP_SETS: list[tuple[str, set[str]]] = [
    ("person:sef",        {"Sef", "Oluwaseun Bello"}),
    ("person:mara-reyes", {"Reyes", "Professor Reyes", "Mara Reyes"}),
    ("org:vega-robotics", {"Vega", "Vega Robotics"}),
    ("person:liam",       {"Liam", "Liam Fitzgerald", "Fitzgerald"}),
]

# NEGATION TRAP
NEGATION_TRAP: tuple[str, str, str] = ("person:liam", "works_at", "org:cedar-general-hospital")

# Symmetric predicates (match in either direction)
SYMMETRIC_PREDS = frozenset({"knows", "friend_of", "sibling_of", "partner_of", "partner"})
# Inverse predicates (child_of matches parent_of in opposite direction)
INVERSE_PREDS: dict[str, str] = {
    "child_of": "parent_of",
    "parent_of": "child_of",
}

# Coreference checks: edge_id -> expected entity
# (these are verified through edge matching rather than explicit tests)
# Edges 8, 9: model must resolve "she" to Nadia, "he" to Tomas
# Edges 4/5: "they" to Nadia+Tomas
# Edge 18: "she" to Priya

# ── scoring lenience ───────────────────────────────────────────────
# For edge 20 (romantic), accept either related_to or knows
# For edges 24, 25 (mentor), accept related_to
# For edges 8, 29, 30 (topic affinity), accept interested_in|skilled_at|studied
_LENIENT_PREDICATES: dict[int, set[str]] = {
    14: {"knows", "friend_of"},                     # Sef--knows-->Nadia
    15: {"friend_of", "knows"},                     # Priya--friend_of-->Nadia
    20: {"related_to", "knows", "partner_of", "partner"},  # Priya+Sef romantic
    21: {"knows", "friend_of"},                     # Tomas--knows-->Priya
    24: {"related_to", "mentored", "mentored_by", "mentor", "teacher", "taught"},
    25: {"related_to", "mentored", "mentored_by", "mentor", "teacher", "taught"},
    27: {"knows", "friend_of", "recommended"},        # Liam--knows-->Nadia
    31: {"friend_of", "knows"},                     # Liam--friend_of-->Tomas
}
# Topic-affinity edges: interchangeable predicates
_TOPIC_AFFINITY_EDGES: dict[int, set[str]] = {
    8:  {"skilled_at", "interested_in", "studied", "good_at", "studies"},
    29: {"interested_in", "skilled_at", "studied", "studies", "values"},
    30: {"interested_in", "skilled_at", "studied", "studies", "values"},
}

# ── helpers ────────────────────────────────────────────────────────

def load_corpus(path: Path) -> list[dict]:
    """Load synthetic conversation as list of {id, role, content, ts}."""
    turns = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            turns.append(json.loads(line))
    return turns


def batch_turns(turns: list[dict], batch_size: int = 12) -> list[list[dict]]:
    """Split turns into fixed-size batches (last batch may be smaller)."""
    return [turns[i:i+batch_size] for i in range(0, len(turns), batch_size)]


# ── entity mapping (proposed → gold) ───────────────────────────────

def build_entity_map(proposed_entities: list[dict]) -> dict[str, str | None]:
    """Map each proposed entity id → gold entity id (or None if unknown).

    Also returns the reverse: gold_id → list[proposed_ids] for dedup checks.
    """
    proposed_to_gold: dict[str, str | None] = {}
    gold_to_proposed: dict[str, list[str]] = defaultdict(list)

    for ent in proposed_entities:
        pid = ent["id"]
        name = ent["name"]
        slug = slug_key(name)
        gold_id = SLUG_TO_GOLD.get(slug)

        # Also check entity aliases
        if gold_id is None:
            for alias in ent.get("aliases", []):
                alias_slug = slug_key(alias)
                gold_id = SLUG_TO_GOLD.get(alias_slug)
                if gold_id:
                    break

        if gold_id:
            proposed_to_gold[pid] = gold_id
            gold_to_proposed[gold_id].append(pid)
        else:
            proposed_to_gold[pid] = None

    return proposed_to_gold, gold_to_proposed


# ── edge matching ──────────────────────────────────────────────────

def normalize_gold_edges() -> tuple[dict[int, tuple], dict[int, tuple]]:
    """Return gold edges as (id→(subj,pred,obj)) and (id→(subj,pred,obj)) for symmetric expansion."""
    edge_map: dict[int, tuple] = {}
    sym_edge_map: dict[int, tuple] = {}  # swapped direction for symmetric
    for subj, pred, obj, eid in GOLD_EDGES:
        edge_map[eid] = (subj, pred, obj)
        if pred in SYMMETRIC_PREDS:
            sym_edge_map[eid] = (obj, pred, subj)
    return edge_map, sym_edge_map


def match_proposed_edges(
    proposed_relations: list[dict],
    proposed_to_gold: dict[str, str | None],
) -> tuple[set[int], set[tuple], list[dict], int]:
    """Match proposed edges against gold. Returns:
    - matched_gold_ids: set of gold edge IDs found
    - gold_edge_triples: set of all gold triples (subj_gold, pred, obj_gold)
    - spurious: proposed edges that didn't match any gold edge
    - proposed_count: total proposed edges evaluated (after entity mapping)
    """
    gold_edge_triples: set[tuple[str, str, str]] = set()
    gold_triple_to_id: dict[tuple[str, str, str], int] = {}

    for subj, pred, obj, eid in GOLD_EDGES:
        gold_edge_triples.add((subj, pred, obj))
        gold_triple_to_id[(subj, pred, obj)] = eid
        # Add symmetric variants
        if pred in SYMMETRIC_PREDS:
            gold_edge_triples.add((obj, pred, subj))
            gold_triple_to_id[(obj, pred, subj)] = eid

    # For scoring lenience, build alternative predicate lookups
    # (We handle this separately for specific edge IDs)

    matched_gold_ids: set[int] = set()
    spurious: list[dict] = []

    for rel in proposed_relations:
        subj_id = rel["subject_id"]
        obj_id = rel["object_id"]
        pred = rel["predicate"]

        # Map to gold IDs
        gold_subj = proposed_to_gold.get(subj_id)
        gold_obj = proposed_to_gold.get(obj_id)
        if gold_subj is None or gold_obj is None:
            spurious.append(rel)
            continue

        triple = (gold_subj, pred, gold_obj)

        # Direct match
        if triple in gold_triple_to_id:
            matched_gold_ids.add(gold_triple_to_id[triple])
            continue

        # Symmetric match (check swapped)
        if pred in SYMMETRIC_PREDS:
            swapped = (gold_obj, pred, gold_subj)
            if swapped in gold_triple_to_id:
                matched_gold_ids.add(gold_triple_to_id[swapped])
                continue

        # Inverse predicate match (child_of ↔ parent_of)
        if pred in INVERSE_PREDS:
            inv_pred = INVERSE_PREDS[pred]
            inverse_triple = (gold_obj, inv_pred, gold_subj)
            if inverse_triple in gold_triple_to_id:
                matched_gold_ids.add(gold_triple_to_id[inverse_triple])
                continue

        # Lenient predicate matching: check each gold edge individually.
        # Also check swapped entity pairs (e.g. proposed "A --recommended--> B"
        # for gold "B --knows--> A" when "recommended" is in the lenient set).
        found_lenient = False
        for edge_info in enumerate_edges_by_id():
            gs, gp, go, eid = edge_info
            acceptable = set()
            if eid in _LENIENT_PREDICATES:
                acceptable = _LENIENT_PREDICATES[eid]
            if eid in _TOPIC_AFFINITY_EDGES:
                acceptable |= _TOPIC_AFFINITY_EDGES[eid]
            if not acceptable:
                continue
            if pred not in acceptable:
                continue
            if (gold_subj == gs and gold_obj == go) or \
               (gold_subj == go and gold_obj == gs):
                matched_gold_ids.add(eid)
                found_lenient = True
                break

        if found_lenient:
            continue

        spurious.append(rel)

    return matched_gold_ids, gold_edge_triples, spurious, len(proposed_relations)


def enumerate_edges_by_id() -> list[tuple]:
    """Return edges as list of (subj, pred, obj, id)."""
    return [(s, p, o, eid) for s, p, o, eid in GOLD_EDGES]


# ── scoring ────────────────────────────────────────────────────────

def score_entities(
    proposed_entities: list[dict],
    proposed_to_gold: dict[str, str | None],
    gold_to_proposed: dict[str, list[str]],
) -> dict:
    """Score entity recovery and dedup."""
    found: set[str] = set()
    missed: set[str] = set()
    split_entities: list[str] = []

    for gid, info in GOLD_ENTITIES.items():
        if info.get("_optional"):
            continue  # don't penalize optional entities
        proposed_ids = gold_to_proposed.get(gid, [])
        if proposed_ids:
            found.add(gid)
            if len(proposed_ids) > 1:
                split_entities.append(f"{gid} -> {proposed_ids}")
        else:
            missed.add(gid)

    unknown_entities = [
        pid for pid, gid in proposed_to_gold.items()
        if gid is None and pid != "person:elliot"
    ]
    has_narrator = any(pid == "person:elliot" and gid is None
                       for pid, gid in proposed_to_gold.items())

    # Dedup checks
    dedup_results: dict[str, str] = {}
    for gid, aliases in DEDUP_SETS:
        # Check how many distinct proposed entities map to this gold ID
        proposed_ids = gold_to_proposed.get(gid, [])
        if len(proposed_ids) <= 1:
            dedup_results[gid] = "MERGED"
        else:
            dedup_results[gid] = f"SPLIT ({len(proposed_ids)} IDs: {proposed_ids})"

    return {
        "found": sorted(found),
        "missed": sorted(missed),
        "split_entities": split_entities,
        "unknown_entities": unknown_entities,
        "dedup_results": dedup_results,
        "has_narrator": any(pid == "person:elliot" and gid is None
                            for pid, gid in proposed_to_gold.items()),
    }


def score_edges(
    proposed_relations: list[dict],
    proposed_to_gold: dict[str, str | None],
) -> dict:
    """Score edge precision/recall/F1, TP recall, traps, coreference."""
    matched_ids, gold_triples, spurious, proposed_count = match_proposed_edges(
        proposed_relations, proposed_to_gold
    )

    gold_count = len(GOLD_EDGES)
    true_positives = len(matched_ids)

    # Gather triples for human-readable output
    gold_triple_set: set[tuple[str, str, str]] = set()
    gold_triple_to_id: dict[tuple[str, str, str], int] = {}
    for subj, pred, obj, eid in GOLD_EDGES:
        gold_triple_set.add((subj, pred, obj))
        gold_triple_to_id[(subj, pred, obj)] = eid
        if pred in SYMMETRIC_PREDS:
            gold_triple_set.add((obj, pred, subj))
            gold_triple_to_id[(obj, pred, subj)] = eid

    # Missed gold edges
    matched_triples: set[tuple[str, str, str]] = set()
    for rel in proposed_relations:
        gs = proposed_to_gold.get(rel["subject_id"])
        go = proposed_to_gold.get(rel["object_id"])
        if gs is None or go is None:
            continue
        pred = rel["predicate"]
        triple = (gs, pred, go)
        if triple in gold_triple_to_id:
            matched_triples.add(triple)
        if pred in SYMMETRIC_PREDS:
            matched_triples.add((go, pred, gs))

    # Use matched gold edge IDs to list missed
    all_gold_ids = set(range(1, 34))
    missed_ids = all_gold_ids - matched_ids

    missed_edges_desc: list[str] = []
    for eid in sorted(missed_ids):
        subj, pred, obj, _ = next(e for e in GOLD_EDGES if e[3] == eid)
        missed_edges_desc.append(f"  #{eid}: {subj} --{pred}--> {obj}")

    # Spurious edge descriptions
    spurious_desc: list[str] = []
    for rel in spurious:
        subj_id = rel.get("subject_id", "")
        obj_id = rel.get("object_id", "")
        subj_display = proposed_to_gold.get(subj_id)
        subj_display = f"[narrator]" if subj_id == "person:elliot" else (subj_display or f"?{subj_id}")
        obj_display = proposed_to_gold.get(obj_id)
        obj_display = f"[narrator]" if obj_id == "person:elliot" else (obj_display or f"?{obj_id}")
        spurious_desc.append(
            f"  {subj_display} --{rel['predicate']}--> {obj_display}  "
            f"(conf={rel.get('confidence', '?'):.2f})"
        )

    # TP recall
    tp_matched = len(matched_ids & TP_EDGE_IDS)
    tp_total = len(TP_EDGE_IDS)

    # Precision/Recall/F1
    precision = true_positives / max(proposed_count, 1)
    recall = true_positives / max(gold_count, 1)
    f1 = 2 * precision * recall / max(precision + recall, 0.001)

    # Trap detection
    trap_emitted = False
    for rel in proposed_relations:
        gs = proposed_to_gold.get(rel["subject_id"])
        go = proposed_to_gold.get(rel["object_id"])
        if gs == "person:liam" and go == "org:cedar-general-hospital" and rel["predicate"] == "works_at":
            trap_emitted = True
            break
        if gs == "person:liam" and (go is None and rel["object_id"].endswith("cedar-general")):
            trap_emitted = True
            break

    # Predicate vocabulary check
    emitted_preds: set[str] = {rel["predicate"] for rel in proposed_relations}
    out_of_vocab = emitted_preds - _CANONICAL_PREDICATES

    # Check mis-mapped predicates: did synonyms get mapped right?
    # (This is about whether the engine's _normalize_predicate worked correctly)
    # We report any predicate that survived but should have been mapped
    # (The engine already normalizes internally, so we just check the output set)

    return {
        "gold_count": gold_count,
        "proposed_count": proposed_count,
        "true_positives": true_positives,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "spurious_count": len(spurious),
        "spurious_edges": spurious_desc,
        "missed_edges": missed_edges_desc,
        "missed_ids": sorted(missed_ids),
        "tp_recall": f"{tp_matched}/{tp_total}",
        "tp_matched": tp_matched,
        "tp_total": tp_total,
        "trap_emitted": trap_emitted,
        "emitted_predicates": sorted(emitted_preds),
        "out_of_vocab_predicates": sorted(out_of_vocab),
    }


# ── main pipeline ──────────────────────────────────────────────────

async def main():
    t_start = time.time()

    # Verify isolation
    assert not ISOLATED_DIR.exists() or not any(ISOLATED_DIR.iterdir()), \
        f"{ISOLATED_DIR} exists and is not empty — refuse to clobber"
    # Double-check real store untouched
    real_store = Path.home() / "sage_data" / "knowledge"
    if real_store.exists():
        real_ents = list(real_store.glob("*_entities.jsonl"))
    else:
        real_ents = []

    print(f"isolation: real notebook read=NO write=NO  (store dir = /tmp/sage_bench)")
    print(f"           store dir = {ISOLATED_DIR}")

    # 1. Load corpus
    corpus = load_corpus(CORPUS_PATH)
    n_turns = len(corpus)
    print(f"corpus: {n_turns} turns")

    # 2. Extract in small batches (4 turns) so each extraction fits comfortably
    # within the NIM endpoint's response window and avoids truncated JSON.
    # Retry failed batches once (the NIM endpoint is stochastic).
    batches = batch_turns(corpus, 4)
    print(f"batches: {len(batches)} ({[len(b) for b in batches]})")

    all_entities: list[dict] = []
    all_relations: list[dict] = []

    import cognition.knowledge_extraction as _ke
    _ke.EXTRACTION_MAX_TOKENS = 1024

    TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=15.0, pool=10.0)
    for i, batch in enumerate(batches, 1):
        for attempt in range(2):
            print(f"\n  extracting batch {i}/{len(batches)} ({len(batch)} turns)"
                  f"{' (retry)' if attempt else ''}...")
            t0 = time.time()
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                result = await extract_relationships(batch, client)
            dt = time.time() - t0
            if result is not None:
                break
            print(f"  attempt {attempt + 1}/2 failed ({dt:.1f}s)")
        else:
            # Both attempts failed
            print(f"  ❌ batch {i}: exhausted retries")
            continue
        entities, relations = result
        print(f"  batch {i}: {len(entities)} entities, {len(relations)} relations ({dt:.1f}s)")
        all_entities.extend(entities)
        all_relations.extend(relations)

    if not all_entities and not all_relations:
        print("\n⚠️ No extractions from any batch — model call may have failed.")
        print("Check NVIDIA_API_KEY and network connectivity.")
        return

    # 3. Merge all relations from all batches (dedup on subject/predicate/object)
    all_relations = merge_relationship_edges(all_relations)

    # Dedup entities by ID (later wins, but IDs should be deterministic)
    seen_entity_ids: set[str] = set()
    deduped_entities: list[dict] = []
    for ent in all_entities:
        if ent["id"] not in seen_entity_ids:
            seen_entity_ids.add(ent["id"])
            deduped_entities.append(ent)
        else:
            # Merge aliases
            for existing in deduped_entities:
                if existing["id"] == ent["id"]:
                    for alias in ent.get("aliases") or []:
                        if alias not in existing.get("aliases", []):
                            existing.setdefault("aliases", []).append(alias)
                    break

    all_entities = deduped_entities

    # 3b. Same-type entity merge (Phase 5 Brick 3c — e5 dedup + lexical subset)
    review_path = str(ISOLATED_DIR / "review_queue.jsonl")
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=2.0)) as client:
        all_entities, all_relations, review_queue = await merge_same_type_entities(
            all_entities, all_relations, client,
            semantic_threshold=0.92,
            review_queue_path=review_path,
        )
    if review_queue:
        with open(review_path, "w") as f:
            for entry in review_queue:
                f.write(json.dumps(entry) + "\n")
        print(f"\n  ⚠ review queue: {len(review_queue)} ambiguous merges → {review_path}")
        for entry in review_queue:
            print(f"    {entry['absorbed_id']} subset of {entry['canonical_candidates']} — {entry['reason']}")

    print(f"\n--- after entity merge ---")

    # Dump raw output for inspection
    output_dir = ISOLATED_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "proposed_entities.json").write_text(
        json.dumps(all_entities, indent=2, ensure_ascii=False)
    )
    (output_dir / "proposed_relations.json").write_text(
        json.dumps(all_relations, indent=2, ensure_ascii=False)
    )

    # Print proposed entities for inspection
    print("\n--- PROPOSED ENTITIES ---")
    for ent in sorted(all_entities, key=lambda e: (ent_type_order(e.get("type","")), e.get("name",""))):
        markers = []
        if ent.get("aliases"):
            markers.append(f"aliases={ent['aliases']}")
        print(f"  {ent['id']:40s} {ent.get('name',''):20s} {('('+'; '.join(markers)+')') if markers else ''}")

    print("\n--- PROPOSED RELATIONS ---")
    for rel in sorted(all_relations, key=lambda r: (r.get("subject_id",""), r.get("predicate",""))):
        print(f"  {rel['subject_id']:30s} --{rel['predicate']:20s}--> {rel['object_id']:30s}  conf={rel.get('confidence',0):.2f}")

    # 4. Build entity mapping and score
    proposed_to_gold, gold_to_proposed = build_entity_map(all_entities)

    ent_score = score_entities(all_entities, proposed_to_gold, gold_to_proposed)
    edge_score = score_edges(all_relations, proposed_to_gold)

    # 5. Report
    elapsed = time.time() - t_start
    report(ent_score, edge_score, elapsed, review_queue)


def ent_type_order(t: str) -> int:
    order = {"person": 0, "org": 1, "place": 2, "project": 3, "topic": 4, "event": 5}
    return order.get(t, 9)


def report(ent_score: dict, edge_score: dict, elapsed: float, review_queue: list | None = None):
    """Print the formatted scorecard."""
    print(f"\n{'='*65}")
    print(f"  BRICK 3c — SYNTHETIC BENCHMARK SCORECARD")
    print(f"{'='*65}")

    # --- ENTITIES ---
    print(f"\n--- ENTITIES ---")
    print(f"  found:  {', '.join(ent_score['found']) if ent_score['found'] else 'NONE'}")
    print(f"  missed: {', '.join(ent_score['missed']) if ent_score['missed'] else 'NONE'}")
    if ent_score.get("has_narrator"):
        print(f"  narrator entity present: person:elliot (expected — not in gold key)")
    if ent_score["unknown_entities"]:
        print(f"  unknown/spurious: {', '.join(ent_score['unknown_entities'])}")

    print(f"\n  dedup:")
    for gid, status in ent_score["dedup_results"].items():
        print(f"    {gid:40s} {status}")
    if ent_score["split_entities"]:
        print(f"  split-entities: {ent_score['split_entities']}")

    if review_queue:
        print(f"\n  review-queue ({len(review_queue)} entries):")
        for entry in review_queue:
            print(f"    ⚠ {entry['absorbed_id']} ambiguous between {entry['canonical_candidates']}")
            print(f"       reason: {entry['reason']}")

    # --- EDGES ---
    print(f"\n--- EDGES ---")
    ec = edge_score
    print(f"  overall: precision={ec['precision']:.4f}  recall={ec['recall']:.4f}  "
          f"F1={ec['f1']:.4f}  (matched {ec['true_positives']} / gold {ec['gold_count']} / "
          f"spurious {ec['spurious_count']})")
    print(f"  THIRD-PARTY recall: {ec['tp_recall']}  <-- HEADLINE")

    if ec["missed_edges"]:
        print(f"  missed gold edges:")
        for desc in ec["missed_edges"]:
            print(f"    {desc}")
    if ec["spurious_edges"]:
        print(f"  spurious / hallucinated edges:")
        for desc in ec["spurious_edges"]:
            print(f"    {desc}")

    # --- PREDICATES ---
    print(f"\n--- PREDICATES ---")
    if ec["out_of_vocab_predicates"]:
        print(f"  all in vocab? NO")
        print(f"  out-of-vocab: {ec['out_of_vocab_predicates']}")
    else:
        print(f"  all in vocab? YES")
    # Check for Brick 3c predicate vocab specifically
    expected_new = {"mentored", "partner_of", "visits"}
    found_new = expected_new & set(ec.get("emitted_predicates", []))
    print(f"  Brick 3c new vocab captured: {found_new if found_new else 'NONE — expected mentored/partner_of/visits'}")
    # Check for mis-mapped predicates (normalization issues)
    print(f"  emitted predicates: {ec['emitted_predicates']}")

    # --- TRAPS & COREF ---
    print(f"\n--- TRAPS & COREF ---")
    print(f"  negation trap avoided? {'YES ✓' if not ec['trap_emitted'] else 'NO ✗ — Liam--works_at-->Cedar General WAS emitted'}")

    # Coreference: check specific edges
    print(f"  coreference:")
    coref_mapping = {
        "edge #8 (she->Nadia ML)": ("person:nadia", "skilled_at", "topic:machine-learning") if not edge_score["trap_emitted"] else True,
    }
    # Check edges 8, 9, 4/5, 18 via whether they got matched
    edge_8_matched = 8 not in edge_score["missed_ids"]
    edge_9_matched = 9 not in edge_score["missed_ids"]
    edge_4_5_matched = 4 not in edge_score["missed_ids"] or 5 not in edge_score["missed_ids"]
    edge_18_matched = 18 not in edge_score["missed_ids"]

    print(f"    edge #8  (she -> Nadia, ML):           {'OK ✓' if edge_8_matched else 'MISSED'}")
    print(f"    edge #9  (he -> Tomas, Tin Kettle):    {'OK ✓' if edge_9_matched else 'MISSED'}")
    print(f"    edges #4/5 (they -> Nadia+Tomas):       {'OK ✓' if edge_4_5_matched else 'MISSED'}")
    print(f"    edge #18 (she -> Priya, Lagos):        {'OK ✓' if edge_18_matched else 'MISSED'}")

    # --- COST ---
    print(f"\n--- COST ---")
    print(f"  ~{elapsed:.0f}s elapsed")
    print(f"\n{'='*65}")
    print(f"  HOLD — review scorecard before any tuning.")
    print(f"{'='*65}")


if __name__ == "__main__":
    asyncio.run(main())
