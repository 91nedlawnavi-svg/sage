"""JSONL → SQLite migration — Wave 2 item 5, Blueprint §5.

The cutover ritual, mechanized:
  1. freeze    — refuse to run if the sage service is active (writes frozen
                 by the operator stopping the unit; this script only checks)
  2. export    — copy every source JSONL into <data>/migration_frozen/
                 (the rollback artifact, kept forever)
  3. import    — JSONL records → SQLite rows, all with actor='migration'
                 audit rows; uuid ids minted, old ids preserved as
                 source_key / legacy_id provenance
  4. verify    — recount both sides; any mismatch = FAIL (exit 1)
  5. switch    — NOT done here: read-path cutover is a code change, deployed
                 separately after this script reports VERIFY OK

Mapping:
  conversation.jsonl        → relational.episodes (source=conversation)
  reflections.jsonl         → interior.episodes  (source=reflection)
  findings.jsonl            → interior.episodes  (source=finding)
  relational entities/rels  → relational.entities/aliases + facts
                              (relation object {kind,value}: entity-kind →
                               object_kind=entity, else literal)
  interior entities/rels    → interior is NOT graph-migrated: the belief
                              ledger replaces the old interior notebook
                              (blueprint §2.5); old interior entities stay
                              readable in the frozen export. Decided with
                              the same Day-0 spirit — her new mind starts
                              from her episodes, not from 2107 derived
                              topic nodes of the old extractor.
  cursors                   → episodes imported as processed=1 when their
                              old key was in the cursor (no re-extraction
                              storm on first heartbeat)

Old backup (~/Documents/old_sage_data) is NEVER touched (Day-0 stands).

Usage:
  python -m memory.migrate_jsonl_to_sqlite --dry-run   # counts only
  python -m memory.migrate_jsonl_to_sqlite             # real run (unit stopped)
  python -m memory.migrate_jsonl_to_sqlite --self-test # temp-store test
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

from config.settings import (
    BASE_DIR, CONVERSATION_PATH, REFLECTIONS_PATH, FINDINGS_PATH, KNOWLEDGE_DIR,
)
import memory.sqlite_core as core
from memory.sqlite_core import writer, query, new_id, now_utc


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _load_cursor(notebook: str) -> set[str]:
    p = KNOWLEDGE_DIR / f"{notebook}_cursor.json"
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text()).get("processed", []))
    except Exception:
        return set()


def service_active() -> bool:
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "sage"],
            capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "active"
    except Exception:
        return False  # can't check (e.g. self-test env) — caller decides


async def migrate(*, dry_run: bool = False, check_service: bool = True) -> dict:
    """Run the ritual. Returns the verify report; raises on any mismatch."""
    # dry-run only counts sources — safe while the unit is live
    if check_service and not dry_run and service_active():
        raise SystemExit("REFUSING: sage unit is active. Stop it first "
                         "(systemctl --user stop sage) — writes must freeze.")

    src = {
        "conversation": _read_jsonl(CONVERSATION_PATH),
        "reflections": _read_jsonl(REFLECTIONS_PATH),
        "findings": _read_jsonl(FINDINGS_PATH),
    }
    import memory.knowledge_store as ks
    rel_entities = ks.load_entities("relational")
    rel_relations = ks.load_relations("relational")
    rel_cursor = _load_cursor("relational")
    int_cursor = _load_cursor("interior")

    report = {
        "src_conversation": len(src["conversation"]),
        "src_reflections": len(src["reflections"]),
        "src_findings": len(src["findings"]),
        "src_rel_entities": len(rel_entities),
        "src_rel_relations": len(rel_relations),
    }
    if dry_run:
        report["dry_run"] = True
        return report

    # 2. export (rollback artifact)
    frozen = BASE_DIR / "migration_frozen"
    frozen.mkdir(parents=True, exist_ok=True)
    for p in (CONVERSATION_PATH, REFLECTIONS_PATH, FINDINGS_PATH):
        if p.exists():
            shutil.copy2(p, frozen / p.name)
    if KNOWLEDGE_DIR.exists():
        shutil.copytree(KNOWLEDGE_DIR, frozen / "knowledge", dirs_exist_ok=True)

    core.ensure_schema("relational")
    core.ensure_schema("interior")

    # 3. import — conversation → relational episodes
    async def _import():
        w_rel = writer("relational")
        w_int = writer("interior")

        def _conv(conn, audit):
            n = 0
            for r in src["conversation"]:
                key = r.get("id")
                text = (r.get("content") or "").strip()
                if not key or not text:
                    continue
                done = 1 if key in rel_cursor else 0
                conn.execute(
                    "INSERT OR IGNORE INTO episodes "
                    "(id, ts, source, speaker, content, processed, extracted, source_key) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (new_id(), r.get("ts") or now_utc(), "conversation",
                     "elliot" if r.get("role") == "user" else "sage",
                     # old cursor = the old extraction pipeline's cursor; those
                     # turns' claims already live in the migrated graph, so mark
                     # extracted too (no re-extraction storm on first beat)
                     text, done, done, key))
                n += 1
            audit("episodes", "bulk", {"imported": n}, _action="migrate")
            return n
        n_conv = await w_rel.submit(_conv, actor="migration", action="migrate")

        def _refl(conn, audit):
            n = 0
            for r in src["reflections"]:
                ts, text = r.get("ts"), (r.get("text") or "").strip()
                if not ts or not text:
                    continue
                key = f"refl:{ts}"
                conn.execute(
                    "INSERT OR IGNORE INTO episodes "
                    "(id, ts, source, content, processed, source_key) "
                    "VALUES (?,?,?,?,?,?)",
                    (new_id(), ts, "reflection", text,
                     1 if key in int_cursor else 0, key))
                n += 1
            for r in src["findings"]:
                ts, q = r.get("ts"), (r.get("query") or "").strip()
                if not ts or not q:
                    continue
                key = f"find:{ts}"
                titles = [(x.get("title") or "").strip()
                          for x in (r.get("results") or [])][:3]
                content = f"Curiosity: {q}"
                if any(titles):
                    content += ". Found: " + "; ".join(t for t in titles if t)
                conn.execute(
                    "INSERT OR IGNORE INTO episodes "
                    "(id, ts, source, content, processed, source_key) "
                    "VALUES (?,?,?,?,?,?)",
                    (new_id(), ts, "finding", content,
                     1 if key in int_cursor else 0, key))
                n += 1
            audit("episodes", "bulk", {"imported": n}, _action="migrate")
            return n
        n_int = await w_int.submit(_refl, actor="migration", action="migrate")

        # relational graph → entities/aliases/facts
        # One-shot phase: a re-run must not mint duplicate uuids for the same
        # legacy rows, so if migrated entities exist we skip wholesale (the
        # audit log is the marker; episodes stay idempotent via source_key).
        def _graph(conn, audit):
            already = conn.execute(
                "SELECT COUNT(*) FROM audit_log "
                "WHERE table_name='entities' AND action='migrate'").fetchone()[0]
            if already:
                return -1, -1  # sentinel: previously migrated, skipped
            id_map: dict[str, str] = {}
            ne = 0
            for e in rel_entities:
                nid = new_id()
                id_map[e["id"]] = nid
                conn.execute(
                    "INSERT INTO entities (id, type, display_name, created_ts) "
                    "VALUES (?,?,?,?)",
                    (nid, e.get("type") or e.get("kind") or "topic",
                     e.get("name") or e["id"], e.get("first_seen") or now_utc()))
                names = {(e.get("name") or e["id"]).lower()}
                names.update(a.lower() for a in (e.get("aliases") or []))
                # legacy name-derived id kept as an alias for lookup continuity
                names.add(e["id"].split(":", 1)[-1].replace("_", " ").lower())
                for a in names:
                    conn.execute(
                        "INSERT OR IGNORE INTO entity_aliases "
                        "(entity_id, alias, added_ts) VALUES (?,?,?)",
                        (nid, a, now_utc()))
                audit("entities", nid, {"legacy_id": e["id"]}, _action="migrate")
                ne += 1
            nf = 0
            for r in rel_relations:
                subj = id_map.get(r.get("subject_id"))
                obj = r.get("object") or {}
                if subj is None:
                    continue
                okind = obj.get("kind") or "literal"
                oval = obj.get("value") or ""
                if okind == "entity":
                    oval = id_map.get(oval, oval)
                else:
                    okind = "literal"
                fid = new_id()
                conn.execute(
                    "INSERT INTO facts (id, subject_entity, predicate, object_kind, "
                    "object_value, epistemic, origin, locked, confidence, valid_from) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (fid, subj, r.get("predicate") or "related_to", okind, oval,
                     "asserted", r.get("origin") or "she",
                     1 if r.get("locked") else 0,
                     float(r.get("confidence") or 0.5),
                     r.get("first_seen") or now_utc()))
                for prov in (r.get("provenance") or []):
                    row = conn.execute(
                        "SELECT id FROM episodes WHERE source_key=?", (prov,)
                    ).fetchone()
                    if row:
                        conn.execute(
                            "INSERT OR IGNORE INTO provenance (claim_id, episode_id) "
                            "VALUES (?,?)", (fid, row["id"]))
                audit("facts", fid, {"legacy_id": r.get("id")}, _action="migrate")
                nf += 1
            return ne, nf
        ne, nf = await w_rel.submit(_graph, actor="migration", action="migrate")
        return n_conv, n_int, ne, nf

    n_conv, n_int, ne, nf = await _import()
    graph_skipped = (ne == -1)

    # 4. verify — db state must equal the SOURCE truth (not this run's
    # insert count), so verification stays meaningful across re-runs.
    exp_conv = sum(1 for r in src["conversation"]
                   if r.get("id") and (r.get("content") or "").strip())
    exp_int = (sum(1 for r in src["reflections"]
                   if r.get("ts") and (r.get("text") or "").strip())
               + sum(1 for r in src["findings"]
                     if r.get("ts") and (r.get("query") or "").strip()))
    exp_ent = len(rel_entities)
    legacy_ids = {e["id"] for e in rel_entities}
    exp_fact = sum(1 for r in rel_relations if r.get("subject_id") in legacy_ids)

    got_conv = query("relational",
        "SELECT COUNT(*) c FROM episodes WHERE source='conversation'")[0]["c"]
    got_int = query("interior", "SELECT COUNT(*) c FROM episodes")[0]["c"]
    got_ent = query("relational", "SELECT COUNT(*) c FROM entities")[0]["c"]
    got_fact = query("relational", "SELECT COUNT(*) c FROM facts")[0]["c"]

    report.update({
        "expected_conversation": exp_conv, "db_conversation": got_conv,
        "expected_interior_episodes": exp_int, "db_interior_episodes": got_int,
        "expected_entities": exp_ent, "db_entities": got_ent,
        "expected_facts": exp_fact, "db_facts": got_fact,
        "graph_phase": "skipped (already migrated)" if graph_skipped else "imported",
        "frozen_dir": str(frozen),
    })

    ok = (got_conv == exp_conv and got_int == exp_int
          and got_ent == exp_ent and got_fact == exp_fact)
    report["verify"] = "OK" if ok else "MISMATCH"
    if not ok:
        raise SystemExit(f"VERIFY MISMATCH: {report}")
    return report


# ── self-test (temp store + temp sources) ─────────────────────────────────
async def _self_test():
    import tempfile
    global CONVERSATION_PATH, REFLECTIONS_PATH, FINDINGS_PATH, KNOWLEDGE_DIR, BASE_DIR
    import memory.knowledge_store as ks

    d = Path(tempfile.mkdtemp(prefix="migrate_test_"))
    core._DB_PATHS = {s: d / f"{s}.db" for s in core.STORES}
    core._writers = {}
    BASE_DIR = d
    CONVERSATION_PATH = d / "conversation.jsonl"
    REFLECTIONS_PATH = d / "reflections.jsonl"
    FINDINGS_PATH = d / "findings.jsonl"
    KNOWLEDGE_DIR = d / "knowledge"
    KNOWLEDGE_DIR.mkdir()

    CONVERSATION_PATH.write_text("\n".join(json.dumps(x) for x in [
        {"id": "user_1", "role": "user", "content": "My sister Maya is a nurse.", "ts": "2026-07-01T00:00:00+00:00"},
        {"id": "assistant_1", "role": "assistant", "content": "Noted!", "ts": "2026-07-01T00:00:05+00:00"},
        {"id": "user_2", "role": "user", "content": "", "ts": "2026-07-01T00:01:00+00:00"},  # empty: skipped
    ]))
    REFLECTIONS_PATH.write_text(json.dumps(
        {"ts": "2026-07-02T00:00:00+00:00", "text": "thinking about gardens", "idle_seconds": 100}))
    FINDINGS_PATH.write_text(json.dumps(
        {"ts": "2026-07-03T00:00:00+00:00", "query": "garden ecology",
         "results": [{"title": "Soil basics"}]}))
    (KNOWLEDGE_DIR / "relational_cursor.json").write_text(
        json.dumps({"processed": ["user_1"]}))

    ks._NOTEBOOK_PATHS["relational"] = (d / "rel_ent.jsonl", d / "rel_rel.jsonl")
    (d / "rel_ent.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"id": "person:maya", "type": "person", "name": "Maya", "aliases": ["sister"],
         "origin": "she", "locked": False, "first_seen": "2026-07-01T00:00:00+00:00"},
        {"id": "person:elliot", "type": "person", "name": "Elliot", "aliases": [],
         "origin": "she", "locked": False, "first_seen": "2026-07-01T00:00:00+00:00"},
    ]))
    (d / "rel_rel.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"id": "r1", "subject_id": "person:maya", "predicate": "works_as",
         "object": {"kind": "literal", "value": "nurse"}, "confidence": 0.9,
         "origin": "she", "locked": True, "provenance": ["user_1"],
         "first_seen": "2026-07-01T00:00:00+00:00"},
        {"id": "r2", "subject_id": "person:elliot", "predicate": "sibling_of",
         "object": {"kind": "entity", "value": "person:maya"}, "confidence": 0.9,
         "origin": "she", "locked": False, "provenance": ["user_1"],
         "first_seen": "2026-07-01T00:00:00+00:00"},
    ]))

    report = await migrate(check_service=False)
    assert report["verify"] == "OK", report
    assert report["db_conversation"] == 2, "empty turn not skipped"
    assert report["db_interior_episodes"] == 2
    assert report["db_entities"] == 2 and report["db_facts"] == 2

    # cursor honored: user_1 pre-processed, assistant_1 pending
    rows = query("relational", "SELECT source_key, processed FROM episodes")
    got = {r["source_key"]: r["processed"] for r in rows}
    assert got["user_1"] == 1 and got["assistant_1"] == 0

    # uuid ids; legacy name reachable via alias; entity-object remapped
    from memory import relational_api as rel
    mayas = rel.find_entities("maya")
    assert len(mayas) == 1 and len(mayas[0]["id"]) == 32
    facts = rel.current_facts(mayas[0]["id"])
    locked = [f for f in facts if f["predicate"] == "works_as"]
    assert locked and locked[0]["locked"] == 1, "locked flag lost in migration"
    ell = rel.find_entities("elliot")[0]
    sib = [f for f in rel.current_facts(ell["id"]) if f["predicate"] == "sibling_of"]
    assert sib and sib[0]["object_value"] == mayas[0]["id"], "entity object not remapped"
    # provenance chain survived
    prov = query("relational",
        "SELECT episode_id FROM provenance WHERE claim_id=?", (locked[0]["id"],))
    assert len(prov) == 1

    # rollback artifact exists
    assert (d / "migration_frozen" / "conversation.jsonl").exists()

    # idempotence: second run adds nothing (INSERT OR IGNORE on source_key)
    r2 = await migrate(check_service=False)
    rows = query("relational",
        "SELECT COUNT(*) c FROM episodes WHERE source='conversation'")
    assert rows[0]["c"] == 2, "re-run duplicated episodes!"

    print("OK migrate self-test")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        asyncio.run(_self_test())
    else:
        rep = asyncio.run(migrate(dry_run="--dry-run" in sys.argv))
        print(json.dumps(rep, indent=2))
