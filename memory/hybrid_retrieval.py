"""Hybrid retrieval — Wave 2, Blueprint §2.7.

Order beats a bigger embedder: structure narrows, similarity ranks.

  1. SQL structural pass: entity-link the query (alias index), pull the
     linked subgraph — facts, gaps, recent episodes — exact and complete.
  2. Embedding rerank WITHIN the candidate set (small sets are where
     cosine works well).
  3. Tier-first: facts and impressions surface by default; raw episodes
     only on explicit ask ("when did I say that?") or when nothing
     distilled matches.
  4. Recency/frequency weighting on top of similarity.

Held-close episodes never leave this module unless the caller explicitly
sets include_held_close=True (the Wave 3 tactful-recall gate will be the
only such caller — §2.8).

Degradation ladder (Invariant 1): embedder down → structural results in
recency order; store down → []. Never raises into chat.
"""
from __future__ import annotations

import math
import re

from memory import relational_api as rel
from memory.sqlite_core import query
from utils.logger import log, warning

# Embedder endpoint is injected by the caller (chat path owns the client);
# these are only shape constants.
EMBED_DIM = 1024

# Similarity floor for the rerank stage. NOTE (blueprint §2.7): the old 0.70
# e5 floor dies with e5 — this floor applies to Qwen3 similarities and gets
# recalibrated during cutover (bench/threshold_calibration.py writes it).
RERANK_SIM_FLOOR = 0.35   # provisional: Qwen3 diff-meaning ceiling measured
                          # 0.33 on the sanity set; calibration refines this.
RECENCY_HALF_LIFE_DAYS = 90.0
STRUCTURAL_EPISODE_LIMIT = 30
DEFAULT_K = 8

_WORD = re.compile(r"[a-zA-ZÀ-ɏ0-9']{2,}")


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


# ── stage 1: structural ───────────────────────────────────────────────────
def structural_candidates(query_text: str, *, include_held_close: bool = False
                          ) -> dict:
    """Entity-link the query via the alias index; pull linked subgraphs.
    Returns {"entities": [...], "facts": [...], "gaps": [...], "episodes": [...]}"""
    out = {"entities": [], "facts": [], "gaps": [], "episodes": []}
    try:
        seen_entities: dict[str, dict] = {}
        for tok in _tokens(query_text):
            for e in rel.find_entities(tok):
                seen_entities.setdefault(e["id"], e)
        # multi-word display names ("New York") — probe bigrams too
        toks = _tokens(query_text)
        for i in range(len(toks) - 1):
            for e in rel.find_entities(f"{toks[i]} {toks[i+1]}"):
                seen_entities.setdefault(e["id"], e)

        out["entities"] = list(seen_entities.values())
        for eid in seen_entities:
            out["facts"].extend(rel.current_facts(eid))
            out["gaps"].extend(rel.open_gaps(eid, limit=5))

        if seen_entities:
            marks = ",".join("?" for _ in seen_entities)
            held = "" if include_held_close else "AND e.held_close=0 "
            rows = query("relational",
                "SELECT DISTINCT e.* FROM episodes e "
                "JOIN provenance p ON p.episode_id=e.id "
                "JOIN facts f ON f.id=p.claim_id "
                f"WHERE f.subject_entity IN ({marks}) {held}"
                "ORDER BY e.ts DESC LIMIT ?",
                (*seen_entities.keys(), STRUCTURAL_EPISODE_LIMIT))
            out["episodes"] = [dict(r) for r in rows]
        return out
    except Exception as exc:
        warning(f"hybrid_retrieval/structural: {type(exc).__name__}: {exc}")
        return out


# ── stage 2+4: rerank within candidates ───────────────────────────────────
def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def _recency_weight(ts: str, now_ts: str) -> float:
    """Exponential decay, half-life RECENCY_HALF_LIFE_DAYS. Naive fallback 1.0."""
    try:
        from datetime import datetime
        t = datetime.fromisoformat(ts)
        now = datetime.fromisoformat(now_ts)
        days = max(0.0, (now - t).total_seconds() / 86400.0)
        return 0.5 ** (days / RECENCY_HALF_LIFE_DAYS)
    except Exception:
        return 1.0


def _fact_text(f: dict) -> str:
    return f"{f['predicate'].replace('_', ' ')} {f['object_value']}"


async def retrieve(query_text: str, embed_fn=None, *, k: int = DEFAULT_K,
                   include_episodes: bool = False,
                   include_held_close: bool = False,
                   now_ts: str | None = None) -> dict:
    """The chat-path entry point.

    embed_fn: async (list[str]) -> list[list[float]] | None. None or a
    failure → structural-only mode (recency order). Never raises.
    """
    from memory.sqlite_core import now_utc
    now_ts = now_ts or now_utc()
    try:
        cand = structural_candidates(query_text,
                                     include_held_close=include_held_close)
        facts, episodes = cand["facts"], cand["episodes"]

        vec_q = None
        if embed_fn is not None and (facts or episodes):
            try:
                texts = [query_text] + [_fact_text(f) for f in facts]
                if include_episodes:
                    texts += [e["content"] for e in episodes]
                vecs = await embed_fn(texts)
                if vecs and len(vecs) == len(texts):
                    vec_q, rest = vecs[0], vecs[1:]
                    for f, v in zip(facts, rest[:len(facts)]):
                        f["_sim"] = _cos(vec_q, v)
                    if include_episodes:
                        for e, v in zip(episodes, rest[len(facts):]):
                            e["_sim"] = _cos(vec_q, v)
            except Exception as exc:
                warning(f"hybrid_retrieval/rerank degraded: {type(exc).__name__}: {exc}")
                vec_q = None

        def _score(item: dict, text_ts: str) -> float:
            sim = item.get("_sim")
            rec = _recency_weight(text_ts, now_ts)
            if sim is None:
                return rec                      # structural-only mode
            if sim < RERANK_SIM_FLOOR:
                return 0.0
            return sim * (0.7 + 0.3 * rec)      # similarity dominates, recency tilts

        for f in facts:
            f["_score"] = _score(f, f.get("valid_from", now_ts))
            # locked facts never rank below derived ones on the same subject
            if f.get("locked"):
                f["_score"] += 1.0
        facts = [f for f in facts if f["_score"] > 0]
        facts.sort(key=lambda f: -f["_score"])

        result = {
            "entities": cand["entities"],
            "facts": facts[:k],
            "gaps": cand["gaps"][:3],           # §2.4: gaps ride along for ask-behavior
            "episodes": [],
            "mode": "hybrid" if vec_q is not None else "structural",
        }
        if include_episodes or not facts:
            eps = episodes
            if vec_q is not None:
                for e in eps:
                    e["_score"] = _score(e, e.get("ts", now_ts))
                eps = [e for e in eps if e.get("_score", 0) > 0]
                eps.sort(key=lambda e: -e.get("_score", 0))
            result["episodes"] = eps[:k]
        log("hybrid_retrieval", "retrieve", mode=result["mode"],
            entities=len(result["entities"]), facts=len(result["facts"]),
            gaps=len(result["gaps"]), episodes=len(result["episodes"]))
        return result
    except Exception as exc:
        warning(f"hybrid_retrieval/retrieve: {type(exc).__name__}: {exc}")
        return {"entities": [], "facts": [], "gaps": [], "episodes": [],
                "mode": "degraded"}


# ── offline self-test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    import tempfile
    from pathlib import Path
    import memory.sqlite_core as core

    async def _main():
        d = Path(tempfile.mkdtemp(prefix="hybrid_test_"))
        core._DB_PATHS = {s: d / f"{s}.db" for s in core.STORES}
        core._writers = {}

        maya = await rel.add_entity(type="person", display_name="Maya",
                                    aliases=["my sister"])
        ep1 = await rel.add_episode(source="conversation", speaker="elliot",
                                    content="Maya just moved to Jakarta.",
                                    source_key="m1")
        ep2 = await rel.add_episode(source="conversation", speaker="elliot",
                                    content="Maya got a nursing job.",
                                    source_key="m2")
        hc = await rel.add_episode(source="conversation", speaker="elliot",
                                   content="heavy thing about Maya",
                                   source_key="m3")
        await rel.set_held_close(hc, True, "elliot-tap", actor="elliot")

        f1 = await rel.add_fact(subject_entity=maya, predicate="lives_in",
                                object_kind="literal", object_value="Jakarta",
                                provenance_episodes=[ep1])
        f2 = await rel.add_fact(subject_entity=maya, predicate="works_as",
                                object_kind="literal", object_value="nurse",
                                provenance_episodes=[ep2])
        f3 = await rel.add_fact(subject_entity=maya, predicate="works_as",
                                object_kind="literal", object_value="pilot",
                                origin="elliot", locked=True, actor="elliot",
                                provenance_episodes=[hc])
        await rel.add_gap(description="when Maya moved to Jakarta",
                          about_entity=maya, spawned_from=f1)

        # structural-only (no embedder): entity-links "Maya", facts + gap come back
        r = await retrieve("How is Maya doing?", None)
        assert r["mode"] == "structural"
        assert len(r["entities"]) == 1
        got_preds = {f["predicate"] for f in r["facts"]}
        assert "lives_in" in got_preds and "works_as" in got_preds
        assert len(r["gaps"]) == 1, "gap didn't ride along"

        # locked wins: pilot (locked) outranks nurse... which lock already retired
        works = [f for f in r["facts"] if f["predicate"] == "works_as"]
        assert len(works) == 1 and works[0]["object_value"] == "pilot"

        # held-close never leaves the module by default
        r = await retrieve("Maya", None, include_episodes=True)
        assert all("heavy thing" not in e["content"] for e in r["episodes"]), \
            "HELD-CLOSE LEAKED"
        r_hc = await retrieve("Maya", None, include_episodes=True,
                              include_held_close=True)
        # (only the tactful-recall gate may set that flag — Wave 3)

        # alias pathway: "my sister" links to Maya
        r = await retrieve("what does my sister do", None)
        assert len(r["entities"]) == 1 and r["entities"][0]["id"] == maya

        # hybrid mode: fake embedder ranks Jakarta-fact above pilot for a
        # Jakarta query — but locked pilot still floats via its bonus... on
        # its own predicate; lives_in must be top of the non-locked ranks.
        async def fake_embed(texts):
            out = []
            for t in texts:
                v = [0.0] * 8
                if "jakarta" in t.lower():
                    v[0] = 1.0
                elif "pilot" in t.lower():
                    v[1] = 1.0
                else:
                    v[2] = 1.0
                out.append(v)
            return out
        r = await retrieve("Is Maya settled in Jakarta?", fake_embed)
        assert r["mode"] == "hybrid"
        assert r["facts"], "hybrid dropped everything"
        nonlocked = [f for f in r["facts"] if not f["locked"]]
        assert nonlocked and nonlocked[0]["predicate"] == "lives_in", \
            "similarity didn't rank Jakarta fact first among derived"

        # embedder mid-call failure degrades to structural, never raises
        async def broken_embed(texts):
            raise RuntimeError("embedder down")
        r = await retrieve("Maya", broken_embed)
        assert r["mode"] == "structural" and r["facts"], "degradation failed"

        # unknown entity → empty but shaped; episodes fallback path exercised
        r = await retrieve("completely unknown topic zzz", None)
        assert r["facts"] == [] and r["entities"] == []

        print("OK hybrid_retrieval self-test")

    asyncio.run(_main())
