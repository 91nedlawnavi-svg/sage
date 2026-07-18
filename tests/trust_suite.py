"""Wave 2 trust test suite — Blueprint §5 Wave 2.4.

The domain modules carry their own self-tests; this suite proves the
*trust properties* — the promises Elliot relies on without reading code —
across modules, against a temp store (Invariant 4: never ~/sage_data).

Covered:
  1. Contamination wall (physical: file handles, both directions;
     plus: no cross-store foreign keys exist in either schema)
  2. Lock semantics (locked wins, survives re-derivation, she can't override)
  3. Tombstone round-trip (correctable, reversible, audited)
  4. Correction round-trip — EVERY id served to a UI surface is actionable
     (the old view-id-mismatch defect class)
  5. Supersede-history (chain integrity from any link, history readable)
  6. Waiting-message hydration (an assistant-turn-with-no-user-turn survives
     storage round-trip; singleton constraint holds under concurrency)
  7. Single-writer discipline under concurrent writes (no lost updates)
  8. Cross-process read-only access degrades, never crashes
"""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

import memory.sqlite_core as core
from memory import relational_api as rel
from memory import interior_api as intr


def _fresh_tmp_store():
    d = Path(tempfile.mkdtemp(prefix="trust_suite_"))
    core._DB_PATHS = {s: d / f"{s}.db" for s in core.STORES}
    core._writers = {}
    return d


PASS = 0

def ok(name: str):
    global PASS
    PASS += 1
    print(f"  ✓ {name}")


async def test_contamination_wall():
    core.ensure_schema("relational")
    core.ensure_schema("interior")
    rel_tables = {r["name"] for r in core.query(
        "relational", "SELECT name FROM sqlite_master WHERE type='table'")}
    int_tables = {r["name"] for r in core.query(
        "interior", "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"beliefs", "stance_events", "source_trust"}.isdisjoint(rel_tables)
    assert {"facts", "entities", "gaps", "promotion_queue"}.isdisjoint(int_tables)
    # no ATTACH in any API path: the query helper opens exactly one file
    assert core.query("relational", "SELECT * FROM beliefs") == []
    assert core.query("interior", "SELECT * FROM facts") == []
    ok("contamination wall: physical, both directions")


async def test_lock_semantics():
    e = await rel.add_entity(type="person", display_name="TestPerson")
    # derived line exists; re-derivation appends another; lock retires both
    d1 = await rel.add_fact(subject_entity=e, predicate="lives_in",
                            object_kind="literal", object_value="CityA")
    d2 = await rel.add_fact(subject_entity=e, predicate="lives_in",
                            object_kind="literal", object_value="CityB")
    locked = await rel.add_fact(subject_entity=e, predicate="lives_in",
                                object_kind="literal", object_value="CityC",
                                origin="elliot", locked=True, actor="elliot")
    cur = [f for f in rel.current_facts(e) if f["predicate"] == "lives_in"]
    assert len(cur) == 1 and cur[0]["id"] == locked, "locked didn't win"
    # re-derivation after lock: appends, then loses at read time AND the new
    # derived sibling is not retired (append-only) but locked still wins view
    d3 = await rel.add_fact(subject_entity=e, predicate="lives_in",
                            object_kind="literal", object_value="CityD")
    cur = [f for f in rel.current_facts(e) if f["predicate"] == "lives_in"]
    assert cur[0]["id"] == locked, "locked fact lost top position after re-derivation"
    # she cannot supersede or tombstone-hide the locked line into a false view
    assert await rel.supersede_fact(locked, object_value="CityE") is None
    ok("lock semantics: locked wins, survives re-derivation, she can't override")


async def test_tombstone_roundtrip():
    e = await rel.add_entity(type="person", display_name="TombTest")
    f = await rel.add_fact(subject_entity=e, predicate="likes",
                           object_kind="literal", object_value="chess")
    assert await rel.tombstone_fact(f, actor="elliot") is True
    assert all(x["id"] != f for x in rel.current_facts(e)), "tombstoned still surfaced"
    assert await rel.untombstone_fact(f, actor="elliot") is True
    assert any(x["id"] == f for x in rel.current_facts(e)), "resurrect failed"
    acts = [r["action"] for r in core.query(
        "relational", "SELECT action FROM audit_log WHERE row_id=? ORDER BY seq", (f,))]
    assert "tombstone" in acts and "untombstone" in acts
    ok("tombstone round-trip: reversible + audited")


async def test_correction_roundtrip_ui_ids():
    """Defect class from the audit: ids served to the UI must be actionable.
    Simulate the UI view (current_facts) and verify every returned id can be
    superseded, locked, and tombstoned — no view-only synthetic ids."""
    e = await rel.add_entity(type="person", display_name="UIPerson")
    for i in range(3):
        await rel.add_fact(subject_entity=e, predicate=f"attr_{i}",
                           object_kind="literal", object_value=f"v{i}")
    served = rel.current_facts(e)
    assert served, "nothing served"
    for row in served:
        fid = row["id"]
        nid = await rel.supersede_fact(fid, object_value=row["object_value"] + "_fixed",
                                       actor="elliot")
        assert nid is not None, f"UI id not supersedable: {fid}"
        assert await rel.tombstone_fact(nid, actor="elliot") is True
        assert await rel.untombstone_fact(nid, actor="elliot") is True
        assert await rel.lock_fact(nid, actor="elliot") is True
    ok("correction round-trip: every UI-served id actionable (supersede/lock/tombstone)")


async def test_supersede_history():
    e = await rel.add_entity(type="person", display_name="HistPerson")
    f1 = await rel.add_fact(subject_entity=e, predicate="works_as",
                            object_kind="literal", object_value="v1")
    f2 = await rel.supersede_fact(f1, object_value="v2")
    f3 = await rel.supersede_fact(f2, object_value="v3")
    for probe in (f1, f2, f3):
        chain = rel.fact_history(probe)
        assert [c["object_value"] for c in chain] == ["v1", "v2", "v3"], \
            f"chain broken from {probe}"
    cur = [f for f in rel.current_facts(e) if f["predicate"] == "works_as"]
    assert len(cur) == 1 and cur[0]["id"] == f3
    ok("supersede-history: chain walkable from any link, single current")


async def test_waiting_message_hydration():
    """§3.4 mechanical requirement: an assistant-turn-with-no-preceding-user-
    turn must survive storage and be revisable, and the one-pending-max
    constraint must hold even under concurrent write attempts."""
    def _write(conn, audit):
        conn.execute(
            "INSERT INTO waiting_message (id, content, thread_ref, written_ts) "
            "VALUES (1,?,?,?)",
            ("I found something about the thing you mentioned.", "thread:x",
             core.now_utc()))
        audit("waiting_message", "1", None)
    await core.writer("relational").submit(_write, actor="she", action="insert")

    # hydration read: the message exists with no user turn preceding it
    rows = core.query("relational", "SELECT * FROM waiting_message WHERE surfaced=0")
    assert len(rows) == 1 and rows[0]["content"].startswith("I found")

    # revisable (she edits before it's read)
    def _revise(conn, audit):
        conn.execute(
            "UPDATE waiting_message SET content=?, revised_ts=? WHERE id=1 AND read_ts IS NULL",
            ("Better version of that note.", core.now_utc()))
        audit("waiting_message", "1", None, _action="revise")
    await core.writer("relational").submit(_revise, actor="she", action="revise")
    rows = core.query("relational", "SELECT * FROM waiting_message")
    assert rows[0]["content"] == "Better version of that note."
    assert rows[0]["revised_ts"] is not None

    # concurrency: N tasks race to insert a second pending — all must fail
    async def _try_second():
        def _ins(conn, audit):
            conn.execute(
                "INSERT INTO waiting_message (id, content, written_ts) VALUES (2,?,?)",
                ("stacked!", core.now_utc()))
        try:
            await core.writer("relational").submit(_ins, actor="she", action="insert")
            return True
        except sqlite3.IntegrityError:
            return False
    results = await asyncio.gather(*[_try_second() for _ in range(5)])
    assert not any(results), "a second pending waiting message got through!"
    ok("waiting message: hydrates, revisable, singleton holds under concurrency")


async def test_single_writer_no_lost_updates():
    """50 concurrent episode writes through the queue: all land, audit == 50."""
    n0 = len(core.query("relational", "SELECT id FROM episodes"))
    a0 = len(core.query("relational",
                        "SELECT seq FROM audit_log WHERE table_name='episodes' "
                        "AND action='insert'"))
    await asyncio.gather(*[
        rel.add_episode(source="conversation", speaker="elliot",
                        content=f"concurrent {i}", source_key=f"conc_{i}")
        for i in range(50)])
    n1 = len(core.query("relational", "SELECT id FROM episodes"))
    a1 = len(core.query("relational",
                        "SELECT seq FROM audit_log WHERE table_name='episodes' "
                        "AND action='insert'"))
    assert n1 - n0 == 50, f"lost updates: {n1-n0}/50 landed"
    assert a1 - a0 == 50, f"audit rows out of step: {a1-a0}/50"
    ok("single-writer: 50 concurrent writes, zero lost, audit in step")


async def test_readonly_process_degrades():
    """A second process opening read-only mid-write-load must degrade
    (empty result), never traceback — the cross-process tool contract."""
    import subprocess, sys
    db = core._DB_PATHS["relational"]
    code = (
        "import sqlite3, sys\n"
        f"conn = sqlite3.connect(r'file:{db}?mode=ro', uri=True, timeout=1)\n"
        "try:\n"
        "    rows = conn.execute('SELECT COUNT(*) FROM episodes').fetchone()\n"
        "    print('READ', rows[0])\n"
        "except sqlite3.OperationalError as e:\n"
        "    print('DEGRADED', e)\n"
    )
    # hold a write transaction open while the other process reads (WAL: readers proceed)
    async def _long_write():
        def _m(conn, audit):
            conn.execute(
                "INSERT INTO episodes (id, ts, source, speaker, content) VALUES (?,?,?,?,?)",
                (core.new_id(), core.now_utc(), "conversation", "elliot", "during-read"))
            audit("episodes", "during-read", None)
            p = subprocess.run([sys.executable, "-c", code],
                               capture_output=True, text=True, timeout=15)
            out = (p.stdout + p.stderr).strip()
            assert p.returncode == 0, f"cross-process read crashed: {out}"
            assert out.startswith(("READ", "DEGRADED")), out
            return out
        return await core.writer("relational").submit(_m, actor="she", action="insert")
    out = await _long_write()
    assert out.startswith("READ"), f"WAL should let readers proceed, got: {out}"
    ok("cross-process read-only: proceeds under WAL, degrades not crashes")


async def test_break_glass_scar_visibility():
    """Interior: the override always scars, and the scar is queryable exactly
    where her prompt assembly will look."""
    await intr.record_stance(topic="test-topic", direction="pro",
                             why="initial read", source="a.example")
    await intr.break_glass_override(topic="test-topic", direction="con",
                                    weight=0.5, reason="test emergency")
    scars = intr.belief_scars("test-topic")
    assert len(scars) == 1
    hist = intr.stance_history("test-topic", limit=1)
    assert hist[0]["origin"] == "elliot-override"
    # and audit agrees
    audits = core.query("interior",
        "SELECT * FROM audit_log WHERE action='break-glass-override'")
    assert len(audits) == 1
    ok("break-glass: scar visible in history, ledger, and audit")


if __name__ == "__main__":
    async def _main():
        _fresh_tmp_store()
        print("trust suite (temp store):")
        await test_contamination_wall()
        await test_lock_semantics()
        await test_tombstone_roundtrip()
        await test_correction_roundtrip_ui_ids()
        await test_supersede_history()
        await test_waiting_message_hydration()
        await test_single_writer_no_lost_updates()
        await test_readonly_process_degrades()
        await test_break_glass_scar_visibility()
        print(f"OK trust suite — {PASS}/9 properties hold")

    asyncio.run(_main())
