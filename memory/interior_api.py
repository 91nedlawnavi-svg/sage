"""Interior store domain API — Wave 2, Blueprint §2.5–§2.6.

Her mind: episodes (reflections/findings/readings), stance events, beliefs,
source trust. Physically separate file (interior.db); nothing here references
relational ids. The contamination wall is the file handle (Blueprint §3.6).

Write-path doctrine:
- Her own pipeline writes freely (origin='she').
- Elliot's arguments land as attributed stance events (origin='elliot-argument')
  — inputs to her thinking, not edits of it.
- The ONLY external edit is the break-glass override (§2.5, amended
  2026-07-17): origin='elliot-override', dated, append-with-history, visible.
  There is deliberately no quiet-edit function in this module.
- Steelman gate: a belief cannot harden until steelman_done is set by a
  recorded opposing-case stance event.
"""
from __future__ import annotations

import json

from memory.sqlite_core import writer, query, new_id, now_utc

STORE = "interior"


# ── episodes (her raw stream) ─────────────────────────────────────────────
async def add_episode(*, source: str, content: str, ts: str | None = None,
                      source_key: str | None = None, actor: str = "she") -> str | None:
    def _m(conn, audit):
        if source_key:
            dup = conn.execute(
                "SELECT id FROM episodes WHERE source_key=?", (source_key,)
            ).fetchone()
            if dup:
                return None
        eid = new_id()
        conn.execute(
            "INSERT INTO episodes (id, ts, source, content, source_key) VALUES (?,?,?,?,?)",
            (eid, ts or now_utc(), source, content, source_key))
        audit("episodes", eid, {"source": source})
        return eid
    return await writer(STORE).submit(_m, actor=actor, action="insert")


def pending_episodes(limit: int = 3) -> list[dict]:
    rows = query(STORE,
        "SELECT * FROM episodes WHERE processed=0 ORDER BY ts LIMIT ?", (limit,))
    return [dict(r) for r in rows]


async def mark_processed(episode_ids: list[str], *, actor: str = "she") -> None:
    def _m(conn, audit):
        for eid in episode_ids:
            conn.execute("UPDATE episodes SET processed=1 WHERE id=?", (eid,))
        audit("episodes", ",".join(episode_ids[:5]) + ("…" if len(episode_ids) > 5 else ""),
              {"n": len(episode_ids)}, _action="mark-processed")
    await writer(STORE).submit(_m, actor=actor, action="mark-processed")


# ── stance events + beliefs (§2.5) ────────────────────────────────────────
async def record_stance(*, topic: str, direction: str, why: str,
                        source: str | None = None,
                        origin: str = "she",
                        is_steelman: bool = False,
                        weight_delta: float = 0.1) -> str:
    """One stance event: something moved her on a topic. Updates/creates the
    belief row and appends belief_history in the same transaction.

    origin: 'she' | 'elliot-argument'. (Overrides go through
    break_glass_override(), never here.)
    """
    assert origin in ("she", "elliot-argument"), "overrides use break_glass_override()"

    def _m(conn, audit):
        sid = new_id()
        ts = now_utc()
        conn.execute(
            "INSERT INTO stance_events (id, ts, topic, source, direction, why, origin) "
            "VALUES (?,?,?,?,?,?,?)",
            (sid, ts, topic, source, direction, why, origin))
        audit("stance_events", sid, {"topic": topic, "direction": direction,
                                     "origin": origin})

        b = conn.execute("SELECT * FROM beliefs WHERE topic=?", (topic,)).fetchone()
        if b is None:
            bid = new_id()
            conn.execute(
                "INSERT INTO beliefs (id, topic, direction, weight, steelman_done, updated_ts) "
                "VALUES (?,?,?,?,?,?)",
                (bid, topic, direction, weight_delta, 1 if is_steelman else 0, ts))
            change = {"created": True, "direction": direction, "weight": weight_delta}
        else:
            bid = b["id"]
            same = (b["direction"] == direction)
            new_weight = max(0.0, b["weight"] + (weight_delta if same else -weight_delta))
            new_dir = b["direction"]
            if not same and new_weight == 0.0:
                new_dir = direction  # weight crossed zero: direction flips, weight rebuilds
                new_weight = weight_delta
            steel = 1 if (is_steelman or b["steelman_done"]) else 0
            conn.execute(
                "UPDATE beliefs SET direction=?, weight=?, steelman_done=?, updated_ts=? "
                "WHERE id=?", (new_dir, new_weight, steel, ts, bid))
            change = {"old": {"direction": b["direction"], "weight": b["weight"]},
                      "new": {"direction": new_dir, "weight": new_weight},
                      "cause": sid}
        conn.execute(
            "INSERT INTO belief_history (belief_id, ts, change, origin) VALUES (?,?,?,?)",
            (bid, ts, json.dumps(change, ensure_ascii=False), origin))
        return sid
    return await writer(STORE).submit(_m, actor=origin, action="stance")


async def harden_belief(topic: str) -> bool:
    """Steelman gate (§2.5): hardening requires steelman_done. Returns whether
    the belief is now hardened."""
    def _m(conn, audit):
        b = conn.execute("SELECT * FROM beliefs WHERE topic=?", (topic,)).fetchone()
        if b is None or not b["steelman_done"]:
            if b is not None:
                audit("beliefs", b["id"], {"refused": "no steelman recorded"},
                      _action="refuse-harden")
            return False
        conn.execute("UPDATE beliefs SET hardened=1, updated_ts=? WHERE id=?",
                     (now_utc(), b["id"]))
        audit("beliefs", b["id"], None, _action="harden")
        return True
    return await writer(STORE).submit(_m, actor="she", action="harden")


async def break_glass_override(*, topic: str, direction: str, weight: float,
                               reason: str) -> str:
    """§2.5 amended 2026-07-17: Elliot's last-resort edit of a belief.
    ALWAYS leaves a scar: origin='elliot-override' in belief_history and a
    stance event she can see and react to. Append-with-history, never silent."""
    def _m(conn, audit):
        ts = now_utc()
        sid = new_id()
        conn.execute(
            "INSERT INTO stance_events (id, ts, topic, source, direction, why, origin) "
            "VALUES (?,?,?,?,?,?,?)",
            (sid, ts, topic, "elliot-override", direction, reason, "elliot-override"))
        b = conn.execute("SELECT * FROM beliefs WHERE topic=?", (topic,)).fetchone()
        if b is None:
            bid = new_id()
            conn.execute(
                "INSERT INTO beliefs (id, topic, direction, weight, updated_ts) "
                "VALUES (?,?,?,?,?)", (bid, topic, direction, weight, ts))
            change = {"created": True, "direction": direction, "weight": weight,
                      "reason": reason}
        else:
            bid = b["id"]
            conn.execute(
                "UPDATE beliefs SET direction=?, weight=?, hardened=0, updated_ts=? "
                "WHERE id=?", (direction, weight, ts, bid))
            change = {"old": {"direction": b["direction"], "weight": b["weight"]},
                      "new": {"direction": direction, "weight": weight},
                      "reason": reason}
        conn.execute(
            "INSERT INTO belief_history (belief_id, ts, change, origin) VALUES (?,?,?,?)",
            (bid, ts, json.dumps(change, ensure_ascii=False), "elliot-override"))
        audit("beliefs", bid, change, _action="break-glass-override")
        return sid
    return await writer(STORE).submit(_m, actor="elliot", action="break-glass-override")


def get_belief(topic: str) -> dict | None:
    rows = query(STORE, "SELECT * FROM beliefs WHERE topic=?", (topic,))
    return dict(rows[0]) if rows else None


def belief_scars(topic: str) -> list[dict]:
    """Override history for a topic — what she sees. Empty = no scars."""
    rows = query(STORE,
        "SELECT h.* FROM belief_history h JOIN beliefs b ON b.id=h.belief_id "
        "WHERE b.topic=? AND h.origin='elliot-override' ORDER BY h.seq", (topic,))
    return [dict(r) for r in rows]


def stance_history(topic: str, limit: int = 20) -> list[dict]:
    rows = query(STORE,
        "SELECT * FROM stance_events WHERE topic=? ORDER BY ts DESC, rowid DESC LIMIT ?",
        (topic, limit))
    return [dict(r) for r in rows]


# ── source trust (§2.6) ───────────────────────────────────────────────────
async def record_source(domain: str, verdict: str) -> None:
    """verdict: 'substance' | 'slop' | 'burned'."""
    assert verdict in ("substance", "slop", "burned")

    def _m(conn, audit):
        ts = now_utc()
        conn.execute(
            "INSERT INTO source_trust (domain, updated_ts) VALUES (?,?) "
            "ON CONFLICT(domain) DO NOTHING", (domain, ts))
        conn.execute(
            f"UPDATE source_trust SET {verdict}={verdict}+1, updated_ts=? WHERE domain=?",
            (ts, domain))
        audit("source_trust", domain, {"verdict": verdict}, _action="record-source")
    await writer(STORE).submit(_m, actor="she", action="record-source")


def source_trust(domain: str) -> dict | None:
    rows = query(STORE, "SELECT * FROM source_trust WHERE domain=?", (domain,))
    return dict(rows[0]) if rows else None


# ── offline self-test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    import tempfile
    from pathlib import Path
    import memory.sqlite_core as core

    async def _main():
        d = Path(tempfile.mkdtemp(prefix="interior_api_test_"))
        core._DB_PATHS = {s: d / f"{s}.db" for s in core.STORES}
        core._writers = {}

        # episodes + dedup
        e1 = await add_episode(source="reflection", content="thinking about taste",
                               source_key="refl:t1")
        assert e1 is not None
        assert await add_episode(source="reflection", content="x",
                                 source_key="refl:t1") is None

        # stance accumulation: sediment, not hot take
        await record_stance(topic="indonesian-politics", direction="reform-sympathetic",
                            why="read two long-form pieces on the 2026 protests",
                            source="tempo.co")
        await record_stance(topic="indonesian-politics", direction="reform-sympathetic",
                            why="follow-up data on turnout", source="bbc.com")
        b = get_belief("indonesian-politics")
        assert b is not None and abs(b["weight"] - 0.2) < 1e-9

        # steelman gate: cannot harden without the opposing case
        assert await harden_belief("indonesian-politics") is False
        assert get_belief("indonesian-politics")["hardened"] == 0
        await record_stance(topic="indonesian-politics", direction="reform-sympathetic",
                            why="read the strongest establishment case; it moved me less",
                            source="kompas.com", is_steelman=True)
        assert await harden_belief("indonesian-politics") is True
        assert get_belief("indonesian-politics")["hardened"] == 1

        # elliot's argument is an input, not an edit
        await record_stance(topic="indonesian-politics", direction="establishment-lean",
                            why="Elliot argued reform coalition overpromises",
                            origin="elliot-argument")
        b = get_belief("indonesian-politics")
        assert b["direction"] == "reform-sympathetic", "one argument flipped her!"
        assert abs(b["weight"] - 0.2) < 1e-9, "counter-stance should reduce weight"

        # counter-weight crossing zero flips direction (fold-on-merit, not on push)
        for _ in range(3):
            await record_stance(topic="indonesian-politics",
                                direction="establishment-lean",
                                why="more counter-evidence",
                                origin="elliot-argument")
        b = get_belief("indonesian-politics")
        assert b["direction"] == "establishment-lean", "direction never flips on merit"

        # break-glass: works, and ALWAYS scars
        assert belief_scars("indonesian-politics") == []
        await break_glass_override(topic="indonesian-politics",
                                   direction="neutral", weight=0.0,
                                   reason="drift emergency test")
        b = get_belief("indonesian-politics")
        assert b["direction"] == "neutral" and b["hardened"] == 0
        scars = belief_scars("indonesian-politics")
        assert len(scars) == 1 and scars[0]["origin"] == "elliot-override"
        sh = stance_history("indonesian-politics", limit=5)
        assert sh[0]["origin"] == "elliot-override", "override invisible to her!"

        # source trust
        await record_source("tempo.co", "substance")
        await record_source("tempo.co", "substance")
        await record_source("contentmill.example", "burned")
        assert source_trust("tempo.co")["substance"] == 2
        assert source_trust("contentmill.example")["burned"] == 1

        # wall check from this side: interior sees no relational tables
        tables = {r["name"] for r in core.query(
            STORE, "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "facts" not in tables and "entities" not in tables, "WALL BREACH"

        print("OK interior_api self-test")

    asyncio.run(_main())
