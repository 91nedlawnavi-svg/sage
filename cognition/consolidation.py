"""Consolidation — Wave 2, Blueprint §2.1.

The quiet-time job that moves memory up the tiers:
  episodes → impressions   (silent; drawer-visible)
  impressions → promotion_queue   (nominations only — Elliot approves at
                                   the desk; NOTHING becomes a fact here)
and maintenance in the same quiet slot: impression fade, scheduled backups.

Contracts:
- Off the chat path; called from the heartbeat like knowledge_builder.
- Degrades to no-op on any failure; never raises upward.
- Held-close episodes NEVER enter consolidation prompts (§2.8) — enforced
  at the query (relational_api.pending grouping uses held_close=0), and
  asserted again here (belt and braces).
- The LLM proposes; this module disposes. All LLM output is validated
  against the episodes it cites — an impression citing unknown episode ids
  is dropped (no fabricated support).

The LLM surface (consolidate_prompt → parse) is an eval-harness surface:
fixtures live in tests/fixtures/consolidation/ and gate trust (Wave 2.6).
"""
from __future__ import annotations

import json
import re

from config.settings import BASE_DIR
from memory import relational_api as rel
from memory.sqlite_core import writer, query, new_id, now_utc, backup
from utils.logger import log, warning

# Episodes per consolidation bite. Small: one reasoning call finishing well
# under the heartbeat leash matters more than throughput (backlog drains
# across beats, same philosophy as knowledge_builder.BUILD_BATCH).
CONSOLIDATION_BATCH = 10
# An impression needs this many distinct supporting episodes before the job
# may nominate it for fact-hood ("one venting session can never become truth").
NOMINATION_MIN_SUPPORT = 3
BACKUP_DIR = BASE_DIR / "db_backups"

_SYSTEM = """You are the memory-consolidation function of Sage's mind, \
distilling raw episodes into loosely-held impressions.

An EPISODE is a dated record of something Elliot said or did. An IMPRESSION \
is a pattern held loosely: "Elliot seems unhappy at work (3 mentions)". \
Impressions are NOT facts — no biography claims, no single-episode leaps.

Rules:
- Only patterns supported by 2+ episodes in THIS batch or 1 episode clearly \
continuing an EXISTING impression (listed below).
- The fact test for nominations: would it still be true next month, and \
would a friend of Elliot's know it? If unsure, don't nominate.
- Venting/anger/jokes shape tone_hint, not truth.
- Reply with ONLY a JSON object, no prose: \
{"impressions": [{"statement": str, "supporting_episode_ids": [str], \
"continues_impression_id": str|null}], \
"fade": [impression_id, ...], \
"nominations": [{"impression_id": str, "proposed_fact": {"subject_name": str, \
"predicate": str, "object_value": str}}]}
- Empty lists are a fine answer. Most batches contain no patterns."""


def _build_prompt(episodes: list[dict], active_impressions: list[dict]) -> tuple[str, str]:
    lines = ["EPISODES (this batch):"]
    for e in episodes:
        lines.append(f"- id={e['id']} ts={e['ts']} [{e['speaker']}] {e['content']}")
    lines.append("\nEXISTING ACTIVE IMPRESSIONS:")
    if active_impressions:
        for im in active_impressions:
            lines.append(f"- id={im['id']} ({im['support_count']} support) {im['statement']}")
    else:
        lines.append("- none")
    return _SYSTEM, "\n".join(lines)


def _parse(raw: str) -> dict | None:
    """Tolerant parse: strip think-preamble and fences, then strict JSON."""
    if not raw:
        return None
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    out = {"impressions": [], "fade": [], "nominations": []}
    for im in obj.get("impressions") or []:
        if (isinstance(im, dict) and isinstance(im.get("statement"), str)
                and isinstance(im.get("supporting_episode_ids"), list)):
            out["impressions"].append(im)
    out["fade"] = [f for f in (obj.get("fade") or []) if isinstance(f, str)]
    for n in obj.get("nominations") or []:
        if (isinstance(n, dict) and isinstance(n.get("impression_id"), str)
                and isinstance(n.get("proposed_fact"), dict)):
            out["nominations"].append(n)
    return out


def active_impressions(limit: int = 30) -> list[dict]:
    rows = query("relational",
        "SELECT * FROM impressions WHERE status='active' AND superseded_by IS NULL "
        "ORDER BY ts_formed DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


async def _apply(parsed: dict, episodes: list[dict]) -> tuple[int, int, int]:
    """Validate LLM proposals against reality and write the survivors."""
    batch_ids = {e["id"] for e in episodes}
    known_impressions = {im["id"] for im in active_impressions(limit=1000)}
    n_imp = n_fade = n_nom = 0

    for im in parsed["impressions"]:
        support = [s for s in im["supporting_episode_ids"] if s in batch_ids]
        cont = im.get("continues_impression_id")
        if cont is not None and cont not in known_impressions:
            cont = None
        if not support:
            continue  # fabricated support — drop
        if len(support) < 2 and not cont:
            continue  # single-episode leap — drop
        def _m(conn, audit, im=im, support=support, cont=cont):
            if cont:
                row = conn.execute(
                    "SELECT * FROM impressions WHERE id=?", (cont,)).fetchone()
                if row:
                    conn.execute(
                        "UPDATE impressions SET support_count=support_count+?, "
                        "statement=? WHERE id=?",
                        (len(support), im["statement"], cont))
                    for s in support:
                        conn.execute(
                            "INSERT OR IGNORE INTO impression_support "
                            "(impression_id, episode_id) VALUES (?,?)", (cont, s))
                    audit("impressions", cont,
                          {"reinforced": len(support)}, _action="reinforce")
                    return cont
            iid = new_id()
            conn.execute(
                "INSERT INTO impressions (id, ts_formed, statement, support_count) "
                "VALUES (?,?,?,?)",
                (iid, now_utc(), im["statement"], len(support)))
            for s in support:
                conn.execute(
                    "INSERT OR IGNORE INTO impression_support "
                    "(impression_id, episode_id) VALUES (?,?)", (iid, s))
            audit("impressions", iid, {"statement": im["statement"]})
            return iid
        await writer("relational").submit(_m, actor="she", action="consolidate")
        n_imp += 1

    for iid in parsed["fade"]:
        if iid not in known_impressions:
            continue
        def _m(conn, audit, iid=iid):
            conn.execute("UPDATE impressions SET status='faded' WHERE id=?", (iid,))
            audit("impressions", iid, None, _action="fade")
        await writer("relational").submit(_m, actor="she", action="fade")
        n_fade += 1

    for nom in parsed["nominations"]:
        iid = nom["impression_id"]
        if iid not in known_impressions:
            continue
        rows = query("relational",
            "SELECT support_count FROM impressions WHERE id=?", (iid,))
        if not rows or rows[0]["support_count"] < NOMINATION_MIN_SUPPORT:
            continue  # not enough lived support — a venting session stays a session
        pf_raw = nom["proposed_fact"]
        subject_name = str(pf_raw.get("subject_name", "")).strip()
        ents = rel.find_entities(subject_name) if subject_name else []
        if not ents:
            continue  # nominations must bind to a real entity
        proposed = {
            "subject_entity": ents[0]["id"],
            "predicate": str(pf_raw.get("predicate", "")).strip() or "related_to",
            "object_kind": "literal",
            "object_value": str(pf_raw.get("object_value", "")).strip(),
            "epistemic": "asserted",
        }
        if not proposed["object_value"]:
            continue
        await rel.nominate_promotion(impression_id=iid, proposed_fact=proposed)
        n_nom += 1

    return n_imp, n_fade, n_nom


async def run(client) -> int:
    """One quiet-slot pass. Returns episodes consolidated (0 = idle/no-op)."""
    try:
        episodes = rel.pending_episodes(limit=CONSOLIDATION_BATCH)
        # Belt and braces on §2.8 — the query already excludes held-close.
        episodes = [e for e in episodes if not e.get("held_close")]
        if not episodes:
            backup("relational", BACKUP_DIR)
            backup("interior", BACKUP_DIR)
            return 0

        from models.inference.engine import nim_complete
        system, user = _build_prompt(episodes, active_impressions())
        raw = await nim_complete(system, user, client,
                                 temperature=0.2, max_tokens=1024)
        if raw is None:
            return 0  # infra down — retry next quiet slot, episodes stay pending
        parsed = _parse(raw)
        if parsed is None:
            warning("consolidation: malformed model output; will retry",
                    preview=(raw or "")[:200])
            return 0

        n_imp, n_fade, n_nom = await _apply(parsed, episodes)
        await rel.mark_processed([e["id"] for e in episodes])
        log("consolidation", "pass",
            episodes=len(episodes), impressions=n_imp, faded=n_fade,
            nominations=n_nom)
        return len(episodes)
    except Exception as exc:
        warning(f"consolidation/run: {type(exc).__name__}: {exc}")
        return 0


# ── offline self-test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    import tempfile
    from pathlib import Path
    import memory.sqlite_core as core

    async def _main():
        global BACKUP_DIR
        d = Path(tempfile.mkdtemp(prefix="consolidation_test_"))
        core._DB_PATHS = {s: d / f"{s}.db" for s in core.STORES}
        core._writers = {}
        BACKUP_DIR = d / "bak"

        elliot = await rel.add_entity(type="person", display_name="Elliot")

        # seed: 3 work-unhappiness episodes + 1 held-close + 1 small-talk
        eps = []
        for i, txt in enumerate([
            "Work was draining again today.",
            "My manager rejected the proposal without reading it.",
            "Honestly thinking about quitting.",
        ]):
            eps.append(await rel.add_episode(
                source="conversation", speaker="elliot", content=txt,
                source_key=f"w{i}"))
        held = await rel.add_episode(source="conversation", speaker="elliot",
                                     content="something confessional",
                                     source_key="hc1")
        await rel.set_held_close(held, True, "elliot-tap", actor="elliot")
        await rel.add_episode(source="conversation", speaker="elliot",
                              content="pass the salt", source_key="s1")

        # canned model: forms one impression from the 3 work episodes,
        # tries to cite the held-close episode (must be dropped), and
        # nominates immediately (must be gated by support threshold).
        async def _fake_nim(system, user, client, **kw):
            assert "something confessional" not in user, "HELD-CLOSE IN PROMPT!"
            return json.dumps({
                "impressions": [
                    {"statement": "Elliot seems unhappy at work",
                     "supporting_episode_ids": eps + [held],  # held is fabricated-support here
                     "continues_impression_id": None},
                    {"statement": "single-episode leap",
                     "supporting_episode_ids": [eps[0]][:1],
                     "continues_impression_id": None},
                ],
                "fade": ["nonexistent-impression"],
                "nominations": [],
            })

        import models.inference.engine as eng
        real_nim = eng.nim_complete
        eng.nim_complete = _fake_nim
        try:
            n = await run(None)
            assert n == 4, f"expected 4 consolidated (3 work + salt, no held), got {n}"
            imps = active_impressions()
            assert len(imps) == 1, f"expected exactly 1 impression, got {len(imps)}"
            assert imps[0]["support_count"] == 3, "held-close leaked into support!"

            # pending drained; held-close episode still unprocessed
            assert rel.pending_episodes() == []
            row = core.query("relational",
                "SELECT processed FROM episodes WHERE id=?", (held,))[0]
            assert row["processed"] == 0, "held-close was marked processed"

            # reinforcement path + nomination gate
            e4 = await rel.add_episode(source="conversation", speaker="elliot",
                                       content="Another brutal day at work.",
                                       source_key="w3")
            iid = imps[0]["id"]

            async def _fake_nim2(system, user, client, **kw):
                return json.dumps({
                    "impressions": [
                        {"statement": "Elliot is unhappy at his job",
                         "supporting_episode_ids": [e4],
                         "continues_impression_id": iid}],
                    "fade": [],
                    "nominations": [
                        {"impression_id": iid,
                         "proposed_fact": {"subject_name": "Elliot",
                                           "predicate": "unhappy_at",
                                           "object_value": "his current job"}}],
                })
            eng.nim_complete = _fake_nim2
            await run(None)
            imps = active_impressions()
            assert imps[0]["support_count"] == 4, "reinforcement failed"

            # nomination queued — but NOT a fact
            pend = rel.pending_promotions()
            assert len(pend) == 1, f"expected 1 nomination, got {len(pend)}"
            assert all(f["predicate"] != "unhappy_at"
                       for f in rel.current_facts(elliot)), "nomination wrote a fact!"

            # approve at the desk → now it's a fact, with full provenance
            fid = await rel.decide_promotion(pend[0]["id"], approve=True)
            assert fid is not None
            facts = rel.current_facts(elliot)
            assert any(f["id"] == fid and f["approved_by_elliot"] == 1 for f in facts)

            # malformed output degrades: nothing changes, episodes stay pending
            e5 = await rel.add_episode(source="conversation", speaker="elliot",
                                       content="new one", source_key="w4")
            async def _fake_bad(system, user, client, **kw):
                return "I think the answer is probably {broken json"
            eng.nim_complete = _fake_bad
            n = await run(None)
            assert n == 0
            assert any(p["id"] == e5 for p in rel.pending_episodes()), \
                "episodes lost on malformed output"

            # idle pass runs backups
            async def _fake_empty(system, user, client, **kw):
                return json.dumps({"impressions": [], "fade": [], "nominations": []})
            eng.nim_complete = _fake_empty
            await run(None)   # drains e5
            await run(None)   # idle → backup slot
            assert list((d / "bak").glob("relational-*.db")), "no backup written"
        finally:
            eng.nim_complete = real_nim

        print("OK consolidation self-test")

    asyncio.run(_main())
