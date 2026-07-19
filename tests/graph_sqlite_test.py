"""graph_sqlite_test.py — flag-gated SQLite mode for backend/api/graph.py.

Temp store only (Invariant 4). Monkeypatches MEMORY_CORE_SQLITE=True and
calls the route handlers directly (plain async functions, asyncio.run).

Covers:
  - seeded node/edge/fact appear in GET /api/graph
  - every served fact id survives confirm → fix → delete round-trip
  - tombstoned facts disappear from the graph
  - merged (merged_into set) entities disappear from the graph
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import memory.sqlite_core as core


def _fresh_tmp_store():
    d = Path(tempfile.mkdtemp(prefix="graph_sqlite_test_"))
    core._DB_PATHS = {s: d / f"{s}.db" for s in core.STORES}
    core._writers = {}
    return d


async def _run():
    _fresh_tmp_store()

    # ── monkeypatch the flag ───────────────────────────────────────────
    import config.settings as settings
    settings.MEMORY_CORE_SQLITE = True

    # Re-import graph module WITH the patched flag visible at call time.
    # The handlers read MEMORY_CORE_SQLITE at call time via the module ref,
    # but _sqlite_graph imports query lazily — no need to reload.
    import backend.api.graph as graph
    # Patch the module-level name the handlers read
    graph.MEMORY_CORE_SQLITE = True

    from memory import relational_api as rel
    from pydantic import BaseModel

    # ── seed data ─────────────────────────────────────────────────────
    # Two persons connected by an entity-object fact (→ edge)
    alice = await rel.add_entity(type="person", display_name="Alice")
    bob   = await rel.add_entity(type="person", display_name="Bob")

    # entity-object fact: Alice knows Bob → edge
    edge_fid = await rel.add_fact(
        subject_entity=alice, predicate="knows",
        object_kind="entity", object_value=bob,
        confidence=0.9,
    )
    # literal fact on Alice: Alice lives_in "Zurich"
    lit_fid = await rel.add_fact(
        subject_entity=alice, predicate="lives_in",
        object_kind="literal", object_value="Zurich",
        confidence=0.9,
    )

    # ── GET /api/graph ─────────────────────────────────────────────────
    result = graph._sqlite_graph(0.6)
    nodes = result["nodes"]
    edges = result["edges"]
    meta  = result["meta"]

    node_ids = {n["id"] for n in nodes}
    assert alice in node_ids, f"Alice missing from nodes: {node_ids}"
    assert bob   in node_ids, f"Bob missing from nodes: {node_ids}"

    edge_ids = {e["id"] for e in edges}
    assert edge_fid in edge_ids, f"edge fact {edge_fid} missing from edges"

    # Alice node should have the literal fact in its facts list
    alice_node = next(n for n in nodes if n["id"] == alice)
    alice_fact_ids = {f["relation_id"] for f in alice_node.get("facts", [])}
    assert lit_fid in alice_fact_ids, f"literal fact {lit_fid} missing from Alice.facts"

    assert meta["mode"] == "sqlite"
    print("  OK graph returns seeded node/edge/fact")

    # ── correction round-trip: every served fact id actionable ─────────
    # Collect all real fact ids served (edges + node facts)
    served_ids: list[str] = []
    for e in edges:
        served_ids.append(e["id"])
    for n in nodes:
        for f in n.get("facts", []):
            served_ids.append(f["relation_id"])

    assert served_ids, "no fact ids were served"

    for fid in served_ids:
        # confirm (lock)
        confirm_body = graph._ConfirmBody(relation_id=fid)
        cr = await graph._sqlite_confirm(confirm_body)
        assert cr["ok"], f"confirm failed for {fid}: {cr}"

        # fix (supersede with new value + lock)
        fix_body = graph._FixBody(relation_id=fid, new_object_value="fixed_value")
        fr = await graph._sqlite_fix(fix_body)
        # fix on a locked fact goes through elliot actor — should succeed
        assert fr["ok"], f"fix failed for {fid}: {fr}"
        new_fid = fr["new_id"]

        # delete (tombstone the new fact)
        del_body = graph._DeleteBody(relation_id=new_fid)
        dr = await graph._sqlite_delete(del_body)
        assert dr["ok"], f"delete failed for {new_fid}: {dr}"

    print("  OK every served fact id survives confirm→fix→delete round-trip")

    # ── tombstoned facts disappear from the graph ───────────────────────
    # Seed a fresh fact, tombstone it, verify it's gone
    alice2 = await rel.add_entity(type="person", display_name="AliceB")
    bob2   = await rel.add_entity(type="person", display_name="BobB")
    fid_live = await rel.add_fact(
        subject_entity=alice2, predicate="knows",
        object_kind="entity", object_value=bob2,
        confidence=0.9,
    )
    result2 = graph._sqlite_graph(0.6)
    assert any(e["id"] == fid_live for e in result2["edges"]), "live fact should be in graph"

    await rel.tombstone_fact(fid_live, actor="elliot")
    result3 = graph._sqlite_graph(0.6)
    assert not any(e["id"] == fid_live for e in result3["edges"]), \
        "tombstoned fact still in graph!"
    print("  OK tombstoned facts disappear from graph")

    # ── merged entities disappear from the graph ────────────────────────
    merged_src = await rel.add_entity(type="person", display_name="MergeMe")
    merged_tgt = await rel.add_entity(type="person", display_name="MergeKeep")
    await rel.add_fact(
        subject_entity=merged_src, predicate="lives_in",
        object_kind="literal", object_value="SomeCity",
        confidence=0.9,
    )
    result4 = graph._sqlite_graph(0.6)
    node_ids4 = {n["id"] for n in result4["nodes"]}
    assert merged_src in node_ids4, "entity should appear before merge"

    await rel.merge_entities(merged_tgt, merged_src, actor="elliot")
    result5 = graph._sqlite_graph(0.6)
    node_ids5 = {n["id"] for n in result5["nodes"]}
    assert merged_src not in node_ids5, "merged-away entity still in graph!"
    print("  OK merged entities disappear from graph")

    print("OK graph_sqlite_test — all assertions passed")


if __name__ == "__main__":
    asyncio.run(_run())
