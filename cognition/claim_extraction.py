"""Claim extraction — Wave 2 wiring, Blueprint §2.2 / §2.4.

The eval-gated surface (extraction-epistemic, 12/12 on the scribe model —
bench/eval_harness.py) put to work: one Elliot episode per call, claims with
epistemic tags out.

Where output lands (§2.1 promotion gate — nothing skips it):
- every claim → promotion_queue via nominate_promotion (Elliot approves at
  the desk; extraction NEVER writes facts directly)
- every gap   → gaps table (silent absences become queryable — §2.4)
- unknown claim subjects → new entities (so nominations can bind)

Cursor: episodes.extracted (schema v2) — independent of `processed`
(consolidation's cursor); the two quiet-time jobs never contend.

Contracts (mirrors knowledge_builder):
- Off the chat path; heartbeat calls run() behind MEMORY_CORE_SQLITE.
- Model/infra failure → episode NOT marked; retried next beat.
- Model replied (even "no claims") → marked extracted.
- Sage's own turns are marked extracted without an LLM call: her speech
  never becomes Elliot-world claims.
- Held-close episodes never reach this module (query excludes them, §2.8).
- Never raises into the heartbeat.
"""
from __future__ import annotations

import json
import re

from config.settings import EXTRACTION_SCRIBE_MODEL
from memory import relational_api as rel
from memory.sqlite_core import writer, query, now_utc
from utils.logger import log, warning

# Elliot episodes fed to the LLM per pass. One call per episode (the shape
# the eval gate scored); small so a pass fits the heartbeat leash.
EXTRACT_BATCH = 4

# The gated prompt — keep byte-identical with bench/eval_harness.py
# _EXTRACT_SYSTEM (the accuracy number belongs to THIS text; editing it here
# silently un-gates the surface).
from bench.eval_harness import _EXTRACT_SYSTEM as SYSTEM_PROMPT
from bench.eval_harness import _parse_json


def _pending(limit: int) -> list[dict]:
    rows = query("relational",
        "SELECT * FROM episodes WHERE extracted=0 AND held_close=0 "
        "AND source='conversation' ORDER BY ts LIMIT ?", (limit,))
    return [dict(r) for r in rows]


async def _mark_extracted(episode_ids: list[str]) -> None:
    def _m(conn, audit):
        for eid in episode_ids:
            conn.execute("UPDATE episodes SET extracted=1 WHERE id=?", (eid,))
        audit("episodes", ",".join(episode_ids[:5]) +
              ("…" if len(episode_ids) > 5 else ""),
              {"n": len(episode_ids)}, _action="mark-extracted")
    await writer("relational").submit(_m, actor="she", action="mark-extracted")


async def _anchor_entity(about: str) -> str | None:
    """Bind a claim's subject to an entity, creating one if unknown.
    'user'/'i'/'me' anchor to Elliot."""
    name = (about or "").strip()
    if not name:
        return None
    if name.lower() in ("user", "speaker", "i", "me", "elliot"):
        name = "Elliot"
    ents = rel.find_entities(name)
    if ents:
        return ents[0]["id"]
    return await rel.add_entity(type="person", display_name=name.title()
                                if name.islower() else name)


def _already_known(subject: str, predicate_value: str) -> bool:
    """Dedup: skip nominating what's already a current fact or already queued."""
    norm = re.sub(r"[^a-z0-9]", "", predicate_value.lower())
    for f in rel.current_facts(subject):
        if re.sub(r"[^a-z0-9]", "", f"{f['predicate']}{f['object_value']}".lower()) == norm:
            return True
    for p in rel.pending_promotions():
        pf = p.get("proposed_fact") or {}
        if pf.get("subject_entity") != subject:
            continue
        if re.sub(r"[^a-z0-9]", "",
                  f"{pf.get('predicate','')}{pf.get('object_value','')}".lower()) == norm:
            return True
    return False


async def _apply(episode: dict, out: dict) -> tuple[int, int]:
    n_nom = n_gap = 0
    for c in out.get("claims") or []:
        if not isinstance(c, dict):
            continue
        predicate = str(c.get("predicate", "")).strip()
        value = str(c.get("value", "")).strip()
        epistemic = c.get("epistemic")
        if not predicate or not value or epistemic not in ("asserted", "believed-by"):
            continue
        subject = await _anchor_entity(str(c.get("about", "")))
        if not subject:
            continue
        if _already_known(subject, predicate + value):
            continue
        proposed = {
            "subject_entity": subject,
            "predicate": predicate,
            "object_kind": "literal",
            "object_value": value,
            "epistemic": epistemic,
        }
        if epistemic == "believed-by":
            proposed["belief_holder"] = str(c.get("belief_holder") or "")
        await rel.nominate_promotion(impression_id=None, proposed_fact=proposed)
        n_nom += 1
    for g in out.get("gaps") or []:
        desc = str(g).strip()
        if desc:
            await rel.add_gap(description=desc, spawned_from=episode["id"])
            n_gap += 1
    return n_nom, n_gap


async def run(client) -> int:
    """One quiet-slot pass. Returns episodes cleared (0 = idle/infra-down)."""
    try:
        pending = _pending(EXTRACT_BATCH)
        if not pending:
            return 0

        from models.inference.engine import nim_complete
        cleared: list[str] = []
        n_nom = n_gap = 0
        for ep in pending:
            if ep["speaker"] != "elliot":
                cleared.append(ep["id"])   # her own words: nothing to extract
                continue
            raw = await nim_complete(
                SYSTEM_PROMPT, f"User turn: {ep['content']}", client,
                model=EXTRACTION_SCRIBE_MODEL, temperature=0.1, max_tokens=2048)
            if raw is None:
                break                      # infra down — stop pass, retry next beat
            out = _parse_json(raw)
            if out is None:
                warning("claim_extraction: malformed output; will retry",
                        episode=ep["id"], preview=raw[:120])
                break
            nn, ng = await _apply(ep, out)
            n_nom += nn
            n_gap += ng
            cleared.append(ep["id"])

        if cleared:
            await _mark_extracted(cleared)
            log("claim_extraction", "pass", episodes=len(cleared),
                nominations=n_nom, gaps=n_gap)
        return len(cleared)
    except Exception as exc:
        warning(f"claim_extraction/run: {type(exc).__name__}: {exc}")
        return 0


# ── offline self-test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    import tempfile
    from pathlib import Path
    import memory.sqlite_core as core

    async def _main():
        d = Path(tempfile.mkdtemp(prefix="claim_extract_test_"))
        core._DB_PATHS = {s: d / f"{s}.db" for s in core.STORES}
        core._writers = {}

        await rel.add_entity(type="person", display_name="Elliot")
        e1 = await rel.add_episode(source="conversation", speaker="elliot",
                                   content="My mom thinks she's British.",
                                   source_key="c1")
        e2 = await rel.add_episode(source="conversation", speaker="sage",
                                   content="That's interesting!", source_key="c2")
        hc = await rel.add_episode(source="conversation", speaker="elliot",
                                   content="confession", source_key="c3")
        await rel.set_held_close(hc, True, "elliot-tap", actor="elliot")

        calls = []

        async def _fake_nim(system, user, client, **kw):
            calls.append(user)
            assert "confession" not in user, "HELD-CLOSE REACHED EXTRACTION"
            return json.dumps({
                "claims": [{"about": "mom", "predicate": "believes_ethnicity",
                            "value": "British", "epistemic": "believed-by",
                            "belief_holder": "mom"}],
                "gaps": ["mom's actual ethnicity"], "tone_hint": None})

        import models.inference.engine as eng
        real = eng.nim_complete
        eng.nim_complete = _fake_nim
        try:
            n = await run(None)
            assert n == 2, f"expected 2 cleared (elliot + sage turn), got {n}"
            assert len(calls) == 1, "sage's own turn hit the LLM"

            # claim → promotion queue, NEVER a direct fact (§2.1 gate)
            pend = rel.pending_promotions()
            assert len(pend) == 1
            pf = pend[0]["proposed_fact"]
            assert pf["epistemic"] == "believed-by" and pf["belief_holder"] == "mom"
            moms = rel.find_entities("mom")
            assert len(moms) == 1, "unknown subject not bound to an entity"
            assert rel.current_facts(moms[0]["id"]) == [], "extraction wrote a fact!"

            # gap landed, spawned from the episode
            gaps = rel.open_gaps()
            assert len(gaps) == 1 and gaps[0]["spawned_from"] == e1

            # idempotent: nothing pending except held-close (never served)
            assert await run(None) == 0

            # dedup: same claim from a new episode doesn't re-nominate
            await rel.add_episode(source="conversation", speaker="elliot",
                                  content="Mom again says she's British.",
                                  source_key="c4")
            await run(None)
            assert len(rel.pending_promotions()) == 1, "duplicate nomination"

            # infra failure: cursor holds
            e5 = await rel.add_episode(source="conversation", speaker="elliot",
                                       content="New fact.", source_key="c5")
            async def _fake_down(system, user, client, **kw):
                return None
            eng.nim_complete = _fake_down
            assert await run(None) == 0
            assert any(p["id"] == e5 for p in _pending(10)), "lost on infra failure"

            # recovery + empty answer advances
            async def _fake_empty(system, user, client, **kw):
                return json.dumps({"claims": [], "gaps": [], "tone_hint": None})
            eng.nim_complete = _fake_empty
            assert await run(None) == 1
            assert not _pending(10)
        finally:
            eng.nim_complete = real

        print("OK claim_extraction self-test")

    asyncio.run(_main())
