"""Relational store domain API — Wave 2, Blueprint §2.1–§2.4.

Operations on Elliot's world: episodes, entities, facts, gaps, promotion.
All writes go through sqlite_core.writer("relational") and audit themselves.
All read helpers degrade to empty on failure (Invariant 1).

Semantics enforced here (the trust rules):
- Facts: supersede-with-history — corrections never delete; the old row gets
  superseded_by and stays queryable as dated history (§2.2).
- Sticky-note locks: a locked fact wins over derived siblings; locking
  retires (tombstones) stale derived rows for the same subject+predicate so
  both can never surface as truth side by side (§2.3, new in v2.0.1).
- Entity identity: uuid ids; merge/split are first-class + audited (§2.3).
- Promotion gate: nothing becomes a fact from consolidation without landing
  in promotion_queue first; approve writes the fact + closes the queue row.
- Gaps are rows, not absences (§2.4).
"""
from __future__ import annotations

import json
from typing import Any

from memory.sqlite_core import writer, query, new_id, now_utc
from utils.logger import warning

STORE = "relational"


# ── episodes ──────────────────────────────────────────────────────────────
async def add_episode(*, source: str, speaker: str, content: str,
                      ts: str | None = None, tone_hint: str | None = None,
                      source_key: str | None = None, actor: str = "she") -> str | None:
    """Append one episode. Returns id, or None if it was a duplicate
    (source_key already present — idempotent intake)."""
    def _m(conn, audit):
        if source_key:
            dup = conn.execute(
                "SELECT id FROM episodes WHERE source_key=?", (source_key,)
            ).fetchone()
            if dup:
                return None
        eid = new_id()
        conn.execute(
            "INSERT INTO episodes (id, ts, source, speaker, content, tone_hint, source_key) "
            "VALUES (?,?,?,?,?,?,?)",
            (eid, ts or now_utc(), source, speaker, content, tone_hint, source_key))
        audit("episodes", eid, {"source": source, "speaker": speaker})
        return eid
    return await writer(STORE).submit(_m, actor=actor, action="insert")


def pending_episodes(limit: int = 12) -> list[dict]:
    """Unprocessed, non-held-close episodes for the extraction pipeline.
    Held-close episodes are NEVER handed to background LLM passes (§2.8)."""
    rows = query(STORE,
        "SELECT * FROM episodes WHERE processed=0 AND held_close=0 "
        "ORDER BY ts LIMIT ?", (limit,))
    return [dict(r) for r in rows]


async def mark_processed(episode_ids: list[str], *, actor: str = "she") -> None:
    def _m(conn, audit):
        for eid in episode_ids:
            conn.execute("UPDATE episodes SET processed=1 WHERE id=?", (eid,))
        audit("episodes", ",".join(episode_ids[:5]) + ("…" if len(episode_ids) > 5 else ""),
              {"n": len(episode_ids)}, _action="mark-processed")
    await writer(STORE).submit(_m, actor=actor, action="mark-processed")


async def set_held_close(episode_id: str, held: bool, origin: str,
                         *, actor: str) -> None:
    """Toggle held-close (§2.8). origin: 'she-sensed' | 'elliot-tap'."""
    def _m(conn, audit):
        conn.execute(
            "UPDATE episodes SET held_close=?, held_close_origin=? WHERE id=?",
            (1 if held else 0, origin if held else None, episode_id))
        audit("episodes", episode_id, {"held": held, "origin": origin},
              _action="toggle-held-close")
    await writer(STORE).submit(_m, actor=actor, action="toggle-held-close")


# ── entities ──────────────────────────────────────────────────────────────
async def add_entity(*, type: str, display_name: str,
                     aliases: list[str] | None = None,
                     actor: str = "she") -> str:
    def _m(conn, audit):
        eid = new_id()
        ts = now_utc()
        conn.execute(
            "INSERT INTO entities (id, type, display_name, created_ts) VALUES (?,?,?,?)",
            (eid, type, display_name, ts))
        for a in dict.fromkeys([display_name.lower(), *(aliases or [])]):
            conn.execute(
                "INSERT OR IGNORE INTO entity_aliases (entity_id, alias, added_ts) "
                "VALUES (?,?,?)", (eid, a.lower(), ts))
        audit("entities", eid, {"type": type, "display_name": display_name})
        return eid
    return await writer(STORE).submit(_m, actor=actor, action="insert")


def find_entities(name_or_alias: str) -> list[dict]:
    """Alias-index lookup; excludes tombstoned and merged-away rows."""
    rows = query(STORE,
        "SELECT DISTINCT e.* FROM entities e "
        "JOIN entity_aliases a ON a.entity_id = e.id "
        "WHERE a.alias = ? AND e.tombstoned=0 AND e.merged_into IS NULL",
        (name_or_alias.lower(),))
    return [dict(r) for r in rows]


async def merge_entities(keep_id: str, absorb_id: str, *, actor: str) -> None:
    """Absorb one entity into another. Provenance-preserving: the absorbed row
    stays (merged_into=keep), its aliases/facts re-point, audit records both."""
    def _m(conn, audit):
        conn.execute("UPDATE entities SET merged_into=? WHERE id=?", (keep_id, absorb_id))
        conn.execute(
            "INSERT OR IGNORE INTO entity_aliases (entity_id, alias, added_ts) "
            "SELECT ?, alias, ? FROM entity_aliases WHERE entity_id=?",
            (keep_id, now_utc(), absorb_id))
        conn.execute("UPDATE facts SET subject_entity=? WHERE subject_entity=?",
                     (keep_id, absorb_id))
        audit("entities", absorb_id, {"merged_into": keep_id}, _action="merge")
    await writer(STORE).submit(_m, actor=actor, action="merge")


async def split_entity(source_id: str, *, new_display_name: str, new_type: str,
                       fact_ids_to_move: list[str], actor: str) -> str:
    """Undo a wrong merge: carve a new entity out and move named facts to it."""
    def _m(conn, audit):
        nid = new_id()
        ts = now_utc()
        conn.execute(
            "INSERT INTO entities (id, type, display_name, created_ts) VALUES (?,?,?,?)",
            (nid, new_type, new_display_name, ts))
        conn.execute(
            "INSERT OR IGNORE INTO entity_aliases (entity_id, alias, added_ts) VALUES (?,?,?)",
            (nid, new_display_name.lower(), ts))
        for fid in fact_ids_to_move:
            conn.execute("UPDATE facts SET subject_entity=? WHERE id=?", (nid, fid))
        audit("entities", nid,
              {"split_from": source_id, "moved_facts": fact_ids_to_move},
              _action="split")
        return nid
    return await writer(STORE).submit(_m, actor=actor, action="split")


# ── facts ─────────────────────────────────────────────────────────────────
async def add_fact(*, subject_entity: str, predicate: str, object_kind: str,
                   object_value: str, epistemic: str = "asserted",
                   origin: str = "she", confidence: float = 1.0,
                   locked: bool = False, provenance_episodes: list[str] | None = None,
                   promoted_from: str | None = None, approved: bool = False,
                   actor: str = "she") -> str:
    """Insert a fact. Locking at insert retires derived same-subject+predicate
    siblings (sticky-note rule §2.3)."""
    def _m(conn, audit):
        fid = new_id()
        conn.execute(
            "INSERT INTO facts (id, subject_entity, predicate, object_kind, object_value, "
            "epistemic, origin, locked, confidence, valid_from, promoted_from, approved_by_elliot) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, subject_entity, predicate, object_kind, object_value, epistemic,
             origin, 1 if locked else 0, confidence, now_utc(), promoted_from,
             1 if approved else 0))
        for ep in provenance_episodes or []:
            conn.execute(
                "INSERT OR IGNORE INTO provenance (claim_id, episode_id) VALUES (?,?)",
                (fid, ep))
        audit("facts", fid, {"predicate": predicate, "locked": locked})
        if locked:
            _retire_derived_siblings(conn, audit, fid, subject_entity, predicate)
        return fid
    return await writer(STORE).submit(_m, actor=actor, action="insert")


def _retire_derived_siblings(conn, audit, winner_id: str,
                             subject: str, predicate: str) -> None:
    """Sticky-note §2.3: a locked fact tombstones live *derived* rows on the
    same subject+predicate so stale lines stop surfacing. History intact."""
    rows = conn.execute(
        "SELECT id FROM facts WHERE subject_entity=? AND predicate=? "
        "AND id != ? AND locked=0 AND origin='she' AND tombstoned=0 "
        "AND superseded_by IS NULL", (subject, predicate, winner_id)).fetchall()
    for r in rows:
        conn.execute("UPDATE facts SET tombstoned=1 WHERE id=?", (r["id"],))
        audit("facts", r["id"], {"retired_by_lock": winner_id}, _action="tombstone")


async def supersede_fact(old_id: str, *, object_value: str,
                         object_kind: str | None = None,
                         epistemic: str | None = None,
                         origin: str = "she", locked: bool = False,
                         provenance_episodes: list[str] | None = None,
                         actor: str = "she") -> str | None:
    """Correction with history (§2.2): new current row, old row demoted but
    never deleted. Returns new id; None if old_id doesn't exist.
    A locked old row can only be superseded by Elliot (actor='elliot')."""
    def _m(conn, audit):
        old = conn.execute("SELECT * FROM facts WHERE id=?", (old_id,)).fetchone()
        if old is None:
            return None
        if old["locked"] and actor != "elliot":
            audit("facts", old_id, {"refused": "locked; she cannot supersede"},
                  _action="refuse-supersede")
            return None
        fid = new_id()
        conn.execute(
            "INSERT INTO facts (id, subject_entity, predicate, object_kind, object_value, "
            "epistemic, origin, locked, confidence, valid_from, approved_by_elliot) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (fid, old["subject_entity"], old["predicate"],
             object_kind or old["object_kind"], object_value,
             epistemic or old["epistemic"], origin, 1 if locked else 0,
             old["confidence"], now_utc(), old["approved_by_elliot"]))
        conn.execute("UPDATE facts SET superseded_by=? WHERE id=?", (fid, old_id))
        for ep in provenance_episodes or []:
            conn.execute(
                "INSERT OR IGNORE INTO provenance (claim_id, episode_id) VALUES (?,?)",
                (fid, ep))
        audit("facts", old_id, {"superseded_by": fid, "new_value": object_value},
              _action="supersede")
        if locked:
            _retire_derived_siblings(conn, audit, fid,
                                     old["subject_entity"], old["predicate"])
        return fid
    return await writer(STORE).submit(_m, actor=actor, action="supersede")


async def lock_fact(fact_id: str, *, actor: str = "elliot") -> bool:
    def _m(conn, audit):
        row = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE facts SET locked=1, origin='elliot-locked' WHERE id=?",
                     (fact_id,))
        audit("facts", fact_id, None, _action="lock")
        _retire_derived_siblings(conn, audit, fact_id,
                                 row["subject_entity"], row["predicate"])
        return True
    return await writer(STORE).submit(_m, actor=actor, action="lock")


async def tombstone_fact(fact_id: str, *, actor: str) -> bool:
    """Soft delete. Reversible: the drawer can resurrect (untombstone)."""
    def _m(conn, audit):
        cur = conn.execute("UPDATE facts SET tombstoned=1 WHERE id=?", (fact_id,))
        if cur.rowcount == 0:
            return False
        audit("facts", fact_id, None, _action="tombstone")
        return True
    return await writer(STORE).submit(_m, actor=actor, action="tombstone")


async def untombstone_fact(fact_id: str, *, actor: str) -> bool:
    def _m(conn, audit):
        cur = conn.execute("UPDATE facts SET tombstoned=0 WHERE id=?", (fact_id,))
        if cur.rowcount == 0:
            return False
        audit("facts", fact_id, None, _action="untombstone")
        return True
    return await writer(STORE).submit(_m, actor=actor, action="untombstone")


def current_facts(subject_entity: str | None = None, *, exclude_held_close: bool = False) -> list[dict]:
    """The live view: not superseded, not tombstoned. Locked-wins ordering."""
    sql = "SELECT * FROM facts WHERE superseded_by IS NULL AND tombstoned=0 "
    params: tuple = ()
    if subject_entity:
        sql += "AND subject_entity=? "
        params = (subject_entity,)
    if exclude_held_close:
        sql += ("AND NOT EXISTS (SELECT 1 FROM provenance p JOIN episodes e "
                "ON e.id=p.episode_id WHERE p.claim_id=facts.id AND e.held_close=1) ")
    sql += "ORDER BY locked DESC, valid_from DESC"
    return [dict(r) for r in query(STORE, sql, params)]


def fact_history(fact_id: str) -> list[dict]:
    """Walk a supersede chain from any link, oldest → newest."""
    rows = query(STORE, "SELECT * FROM facts")
    by_id = {r["id"]: dict(r) for r in rows}
    if fact_id not in by_id:
        return []
    # walk back to root
    back = {r["superseded_by"]: r["id"] for r in rows if r["superseded_by"]}
    cur = fact_id
    while cur in back:
        cur = back[cur]
    # walk forward
    chain = [by_id[cur]]
    while by_id[cur]["superseded_by"]:
        cur = by_id[cur]["superseded_by"]
        chain.append(by_id[cur])
    return chain


# ── gaps (§2.4) ───────────────────────────────────────────────────────────
async def add_gap(*, description: str, about_entity: str | None = None,
                  spawned_from: str | None = None, actor: str = "she") -> str:
    def _m(conn, audit):
        gid = new_id()
        conn.execute(
            "INSERT INTO gaps (id, about_entity, description, spawned_from, created_ts) "
            "VALUES (?,?,?,?,?)",
            (gid, about_entity, description, spawned_from, now_utc()))
        audit("gaps", gid, {"description": description})
        return gid
    return await writer(STORE).submit(_m, actor=actor, action="insert")


async def answer_gap(gap_id: str, *, fact_id: str, actor: str = "she") -> None:
    def _m(conn, audit):
        conn.execute(
            "UPDATE gaps SET status='answered', answered_by=? WHERE id=?",
            (fact_id, gap_id))
        audit("gaps", gap_id, {"answered_by": fact_id}, _action="answer")
    await writer(STORE).submit(_m, actor=actor, action="answer")


def open_gaps(about_entity: str | None = None, limit: int = 10,
              *, exclude_held_close: bool = False) -> list[dict]:
    sql = "SELECT * FROM gaps WHERE status='open' "
    params: tuple = ()
    if about_entity:
        sql += "AND about_entity=? "
        params = (about_entity,)
    if exclude_held_close:
        sql += ("AND NOT EXISTS (SELECT 1 FROM episodes e WHERE e.id=gaps.spawned_from "
                "AND e.held_close=1) "
                "AND NOT EXISTS (SELECT 1 FROM provenance p JOIN episodes e "
                "ON e.id=p.episode_id WHERE p.claim_id=gaps.spawned_from "
                "AND e.held_close=1) ")
    sql += "ORDER BY created_ts DESC LIMIT ?"
    return [dict(r) for r in query(STORE, sql, (*params, limit))]


# ── promotion desk (§2.1) ─────────────────────────────────────────────────
async def nominate_promotion(*, impression_id: str | None,
                             proposed_fact: dict, actor: str = "she") -> str:
    """Consolidation nominates; NOTHING becomes a fact here. Queue only."""
    def _m(conn, audit):
        pid = new_id()
        conn.execute(
            "INSERT INTO promotion_queue (id, impression_id, proposed_fact, nominated_ts) "
            "VALUES (?,?,?,?)",
            (pid, impression_id, json.dumps(proposed_fact, ensure_ascii=False), now_utc()))
        audit("promotion_queue", pid, {"proposed": proposed_fact}, _action="nominate")
        return pid
    return await writer(STORE).submit(_m, actor=actor, action="nominate")


def pending_promotions() -> list[dict]:
    rows = query(STORE,
        "SELECT * FROM promotion_queue WHERE status='pending' ORDER BY nominated_ts")
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["proposed_fact"] = json.loads(d["proposed_fact"])
        except Exception:
            pass
        out.append(d)
    return out


async def decide_promotion(queue_id: str, *, approve: bool,
                           actor: str = "elliot") -> str | None:
    """Elliot's call at the desk. Approve → the fact is written (approved flag
    set, promotion provenance kept). Reject → queue row closed, nothing writes.
    Returns new fact id on approval, None otherwise."""
    def _m(conn, audit):
        row = conn.execute(
            "SELECT * FROM promotion_queue WHERE id=? AND status='pending'",
            (queue_id,)).fetchone()
        if row is None:
            return None
        status = "approved" if approve else "rejected"
        conn.execute(
            "UPDATE promotion_queue SET status=?, decided_ts=? WHERE id=?",
            (status, now_utc(), queue_id))
        audit("promotion_queue", queue_id, {"decision": status}, _action=status)
        if not approve:
            return None
        pf = json.loads(row["proposed_fact"])
        fid = new_id()
        conn.execute(
            "INSERT INTO facts (id, subject_entity, predicate, object_kind, object_value, "
            "epistemic, origin, confidence, valid_from, promoted_from, approved_by_elliot) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1)",
            (fid, pf["subject_entity"], pf["predicate"], pf.get("object_kind", "literal"),
             pf["object_value"], pf.get("epistemic", "asserted"), "she",
             pf.get("confidence", 1.0), now_utc(), row["impression_id"]))
        audit("facts", fid, {"promoted_from_queue": queue_id}, _action="promote")
        if row["impression_id"]:
            conn.execute("UPDATE impressions SET status='promoted' WHERE id=?",
                         (row["impression_id"],))
        return fid
    return await writer(STORE).submit(_m, actor=actor, action="decide-promotion")


def held_close_source_keys() -> set[str] | None:
    """Return held-close source keys, or None when authority is unavailable."""
    try:
        from memory.sqlite_core import _connect
        conn = _connect(STORE, readonly=True)
        try:
            rows = conn.execute(
                "SELECT source_key FROM episodes WHERE held_close=1 AND source_key IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
        return {r["source_key"] for r in rows}
    except Exception as exc:
        warning(f"relational_api/held_close_source_keys: {type(exc).__name__}: {exc}")
        return None


async def set_held_close_by_source_key(source_key: str, held: bool,
                                       *, actor: str) -> bool:
    """Toggle held-close for the episode matching source_key. Returns True if found."""
    def _m(conn, audit):
        row = conn.execute(
            "SELECT id FROM episodes WHERE source_key=?", (source_key,)).fetchone()
        if row is None:
            return False
        eid = row["id"]
        origin = "elliot-tap" if actor == "elliot" else "she-sensed"
        conn.execute(
            "UPDATE episodes SET held_close=?, held_close_origin=? WHERE id=?",
            (1 if held else 0, origin if held else None, eid))
        audit("episodes", eid, {"held": held, "origin": origin},
              _action="toggle-held-close")
        return True
    return await writer(STORE).submit(_m, actor=actor, action="toggle-held-close")


# ── waiting message (§3.4) ────────────────────────────────────────────────
async def set_waiting_message(content: str, thread_ref: str | None = None,
                              *, actor: str = "she") -> None:
    """Write or revise the one pending waiting message (§3.4: one max, revisable).

    Uses id=1 with ON CONFLICT REPLACE so only one row ever exists.
    """
    from memory.sqlite_core import now_utc as _now
    def _m(conn, audit):
        ts = _now()
        existing = conn.execute("SELECT id FROM waiting_message WHERE id=1").fetchone()
        if existing:
            conn.execute(
                "UPDATE waiting_message SET content=?, thread_ref=?, revised_ts=?, "
                "surfaced=0, read_ts=NULL WHERE id=1",
                (content, thread_ref, ts))
            audit("waiting_message", "1", {"action": "revised"})
        else:
            conn.execute(
                "INSERT INTO waiting_message (id, content, thread_ref, written_ts) "
                "VALUES (1,?,?,?)", (content, thread_ref, ts))
            audit("waiting_message", "1", {"action": "created"})
    await writer(STORE).submit(_m, actor=actor, action="set-waiting-message")


def get_waiting_message() -> dict | None:
    """Return the pending waiting message (unread only), or None."""
    rows = query(STORE,
        "SELECT * FROM waiting_message WHERE id=1 AND surfaced=0 AND read_ts IS NULL")
    return dict(rows[0]) if rows else None


async def mark_waiting_message_surfaced(*, actor: str = "she") -> None:
    """Mark the waiting message as surfaced (shown to Elliot)."""
    def _m(conn, audit):
        conn.execute("UPDATE waiting_message SET surfaced=1 WHERE id=1")
        audit("waiting_message", "1", None, _action="surfaced")
    await writer(STORE).submit(_m, actor=actor, action="surface-waiting-message")


async def mark_waiting_message_read(*, actor: str = "elliot") -> None:
    """Mark the waiting message as read (Elliot replied)."""
    from memory.sqlite_core import now_utc as _now
    def _m(conn, audit):
        conn.execute("UPDATE waiting_message SET read_ts=? WHERE id=1", (_now(),))
        audit("waiting_message", "1", None, _action="read")
    await writer(STORE).submit(_m, actor=actor, action="read-waiting-message")


# ── threads (§3.1) ────────────────────────────────────────────────────────
THREAD_INITIAL_HEAT = 1.0
# Decay runs once per quiet heartbeat (~60s). 0.05/beat killed a fresh thread
# in 20 minutes — threads are supposed to carry heat across DAYS (§3.1).
# 0.0006/beat ≈ 1.0 → stale(0.1) over ~25h of quiet; any feed resets the clock.
THREAD_DECAY_PER_BEAT = 0.0006
THREAD_HOT_THRESHOLD = 0.7    # above this → eligible for burst search
THREAD_STALE_THRESHOLD = 0.1  # below this → auto-stale
# Max share of weekly thread-heat any single thread can hold
THREAD_PORTFOLIO_FLOOR = 0.5


async def open_thread(question: str, *, spawned_from: str | None = None,
                      spawn_kind: str | None = None,
                      actor: str = "she") -> str:
    """Open a new thread. Returns id."""
    def _m(conn, audit):
        tid = new_id()
        ts = now_utc()
        conn.execute(
            "INSERT INTO threads (id, question, heat, spawned_from, spawn_kind, "
            "created_ts, updated_ts) VALUES (?,?,?,?,?,?,?)",
            (tid, question, THREAD_INITIAL_HEAT, spawned_from, spawn_kind, ts, ts))
        audit("threads", tid, {"question": question[:80], "spawn_kind": spawn_kind})
        return tid
    return await writer(STORE).submit(_m, actor=actor, action="open-thread")


async def feed_thread(thread_id: str, heat_delta: float = 0.3,
                      *, actor: str = "she") -> None:
    """A finding or reflection touched this thread — raise its heat."""
    def _m(conn, audit):
        ts = now_utc()
        row = conn.execute("SELECT heat FROM threads WHERE id=?", (thread_id,)).fetchone()
        if row is None:
            return
        new_heat = min(2.0, row["heat"] + heat_delta)
        conn.execute("UPDATE threads SET heat=?, updated_ts=? WHERE id=?",
                     (new_heat, ts, thread_id))
        audit("threads", thread_id, {"heat_delta": heat_delta, "new_heat": new_heat},
              _action="feed")
    await writer(STORE).submit(_m, actor=actor, action="feed-thread")


async def resolve_thread(thread_id: str, conclusion: str,
                         *, actor: str = "she") -> None:
    """Archive a thread as resolved with a conclusion."""
    def _m(conn, audit):
        ts = now_utc()
        conn.execute(
            "UPDATE threads SET status='resolved', conclusion=?, "
            "heat=0, resolved_ts=?, updated_ts=? WHERE id=?",
            (conclusion, ts, ts, thread_id))
        audit("threads", thread_id, {"conclusion": conclusion[:120]},
              _action="resolve")
    await writer(STORE).submit(_m, actor=actor, action="resolve-thread")


def hot_threads(limit: int = 5) -> list[dict]:
    """Return open threads by heat descending."""
    rows = query(STORE,
        "SELECT * FROM threads WHERE status='open' ORDER BY heat DESC LIMIT ?",
        (limit,))
    return [dict(r) for r in rows]


def all_open_threads() -> list[dict]:
    rows = query(STORE, "SELECT * FROM threads WHERE status='open' ORDER BY heat DESC")
    return [dict(r) for r in rows]


async def decay_threads(*, actor: str = "she") -> int:
    """Apply per-beat heat decay; stale threads below threshold auto-close.

    Returns number staled. Called from the heartbeat quiet slot.
    """
    def _m(conn, audit):
        ts = now_utc()
        rows = conn.execute(
            "SELECT id, heat FROM threads WHERE status='open'").fetchall()
        staled = 0
        for r in rows:
            new_heat = max(0.0, r["heat"] - THREAD_DECAY_PER_BEAT)
            if new_heat < THREAD_STALE_THRESHOLD:
                conn.execute(
                    "UPDATE threads SET status='stale', heat=0, updated_ts=? WHERE id=?",
                    (ts, r["id"]))
                audit("threads", r["id"], {"staled": True}, _action="stale")
                staled += 1
            else:
                conn.execute(
                    "UPDATE threads SET heat=?, updated_ts=? WHERE id=?",
                    (new_heat, ts, r["id"]))
        return staled
    return await writer(STORE).submit(_m, actor=actor, action="decay-threads")


# ── offline self-test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    import tempfile
    from pathlib import Path
    import memory.sqlite_core as core

    async def _main():
        d = Path(tempfile.mkdtemp(prefix="relational_api_test_"))
        core._DB_PATHS = {s: d / f"{s}.db" for s in core.STORES}
        core._writers = {}

        # episodes: idempotent intake via source_key
        e1 = await add_episode(source="conversation", speaker="elliot",
                               content="My sister Maya lives in Bandung.",
                               source_key="user_1")
        dup = await add_episode(source="conversation", speaker="elliot",
                                content="dup", source_key="user_1")
        assert e1 and dup is None, "source_key dedup failed"

        # held-close never reaches the pipeline
        e2 = await add_episode(source="conversation", speaker="elliot",
                               content="something heavy", source_key="user_2")
        await set_held_close(e2, True, "elliot-tap", actor="elliot")
        pend = pending_episodes()
        assert all(p["id"] != e2 for p in pend), "held-close leaked to pipeline!"
        assert any(p["id"] == e1 for p in pend)

        # entities: two Mayas stay two people
        maya1 = await add_entity(type="person", display_name="Maya")
        maya2 = await add_entity(type="person", display_name="Maya")
        assert maya1 != maya2
        assert len(find_entities("maya")) == 2, "same-name entities collapsed!"

        # merge is provenance-preserving; find() hides the absorbed row
        await merge_entities(maya1, maya2, actor="elliot")
        assert len(find_entities("maya")) == 1

        # facts: supersede-with-history
        f1 = await add_fact(subject_entity=maya1, predicate="lives_in",
                            object_kind="literal", object_value="Bandung",
                            provenance_episodes=[e1])
        f2 = await supersede_fact(f1, object_value="Jakarta")
        cur = current_facts(maya1)
        assert len(cur) == 1 and cur[0]["object_value"] == "Jakarta"
        hist = fact_history(f1)
        assert [h["object_value"] for h in hist] == ["Bandung", "Jakarta"]
        hist_from_new = fact_history(f2)
        assert hist == hist_from_new, "history differs by entry link"

        # sticky-note: lock retires derived sibling; she cannot supersede a lock
        f3 = await add_fact(subject_entity=maya1, predicate="works_as",
                            object_kind="literal", object_value="teacher")
        f4 = await add_fact(subject_entity=maya1, predicate="works_as",
                            object_kind="literal", object_value="nurse",
                            origin="elliot", locked=True, actor="elliot")
        cur = [f for f in current_facts(maya1) if f["predicate"] == "works_as"]
        assert len(cur) == 1 and cur[0]["id"] == f4, "locked fact didn't retire sibling"
        blocked = await supersede_fact(f4, object_value="pilot")  # she tries
        assert blocked is None, "she superseded a locked fact!"
        ok = await supersede_fact(f4, object_value="pilot", actor="elliot")
        assert ok is not None, "elliot couldn't supersede his own lock"

        # lock_fact retires the remaining derived sibling on the same predicate
        f5 = await add_fact(subject_entity=maya1, predicate="likes",
                            object_kind="literal", object_value="tea")
        f6 = await add_fact(subject_entity=maya1, predicate="likes",
                            object_kind="literal", object_value="coffee")
        assert await lock_fact(f5, actor="elliot") is True
        likes = [f for f in current_facts(maya1) if f["predicate"] == "likes"]
        assert len(likes) == 1 and likes[0]["id"] == f5, "lock_fact didn't retire sibling"

        # tombstone round-trip
        assert await tombstone_fact(f3, actor="elliot") in (True, False)
        assert await untombstone_fact(f3, actor="elliot") is True

        # gaps
        g = await add_gap(description="when Maya moved to Jakarta",
                          about_entity=maya1, spawned_from=f2)
        assert len(open_gaps(maya1)) == 1
        await answer_gap(g, fact_id=f2)
        assert len(open_gaps(maya1)) == 0

        # promotion desk: nominate → not a fact until approved
        n_before = len(current_facts(maya1))
        q = await nominate_promotion(impression_id=None, proposed_fact={
            "subject_entity": maya1, "predicate": "hobby",
            "object_kind": "literal", "object_value": "painting"})
        assert len(current_facts(maya1)) == n_before, "nomination wrote a fact!"
        assert len(pending_promotions()) == 1
        fid = await decide_promotion(q, approve=True)
        assert fid is not None
        facts_now = current_facts(maya1)
        promoted = [f for f in facts_now if f["id"] == fid][0]
        assert promoted["approved_by_elliot"] == 1
        assert len(pending_promotions()) == 0

        # reject path writes nothing
        q2 = await nominate_promotion(impression_id=None, proposed_fact={
            "subject_entity": maya1, "predicate": "hobby2",
            "object_kind": "literal", "object_value": "chess"})
        r = await decide_promotion(q2, approve=False)
        assert r is None
        assert all(f["predicate"] != "hobby2" for f in current_facts(maya1))

        # audit log recorded every mutation class used above
        actions = {r["action"] for r in core.query(STORE, "SELECT action FROM audit_log")}
        for needed in ("insert", "supersede", "lock", "tombstone", "merge",
                       "nominate", "approved", "promote", "toggle-held-close"):
            assert needed in actions, f"audit missing action: {needed}"

        print("OK relational_api self-test")

    asyncio.run(_main())
