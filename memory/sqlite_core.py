"""SQLite memory core — Wave 2, Blueprint §2.

Engine-room layer only: connections, pragmas, single-writer discipline,
migrations (schema versioning), and the audit contract. Domain APIs
(episodes, facts, entities, beliefs) live in sibling modules and go through
this one for every write.

Contracts (Blueprint §5 Wave 2.1):
- Two physical stores: relational.db (Elliot's world) and interior.db
  (Sage's mind). The contamination wall is enforced by file handles — a
  connection is opened on exactly one store and cannot see the other.
- WAL mode, busy_timeout, foreign keys ON.
- Single-writer: all writes funnel through an asyncio queue per store;
  readers never block writers (WAL) and writers never interleave.
- Every mutation records an audit row IN THE SAME TRANSACTION.
- All timestamps stored as UTC tz-aware ISO8601 strings.
- Graceful degradation: public read helpers return empty on any failure;
  writes raise to their caller (background jobs), never into chat/heartbeat
  paths directly.
- Day-0: both files live under SAGE_DATA_DIR; rm -rf wipes them.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from config.settings import BASE_DIR
from utils.logger import log, warning

STORES = ("relational", "interior")

_DB_PATHS = {
    "relational": BASE_DIR / "relational.db",
    "interior": BASE_DIR / "interior.db",
}

BUSY_TIMEOUT_MS = 5000


def now_utc() -> str:
    """Canonical storage timestamp: UTC, tz-aware, ISO8601, seconds precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    """Stable random id — never derived from display names (Blueprint §2.3)."""
    return uuid.uuid4().hex


def _connect(store: str, *, readonly: bool = False) -> sqlite3.Connection:
    if store not in STORES:
        raise ValueError(f"unknown store: {store!r}")
    path = _DB_PATHS[store]
    # check_same_thread=False: the writer connection is created on the event
    # loop but used inside asyncio.to_thread; the per-store asyncio.Lock
    # already guarantees one user at a time, which is the safety that flag
    # would otherwise enforce.
    if readonly:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_MS / 1000,
            check_same_thread=False,
        )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            path, timeout=BUSY_TIMEOUT_MS / 1000, check_same_thread=False
        )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ── schema ────────────────────────────────────────────────────────────────
# Versioned, forward-only migrations. user_version tracks the applied tip.

_RELATIONAL_V1 = """
CREATE TABLE episodes (
    id          TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    source      TEXT NOT NULL,
    speaker     TEXT NOT NULL,
    content     TEXT NOT NULL,
    tone_hint   TEXT,
    held_close  INTEGER NOT NULL DEFAULT 0,
    held_close_origin TEXT,
    processed   INTEGER NOT NULL DEFAULT 0,
    source_key  TEXT UNIQUE
);
CREATE INDEX idx_episodes_ts ON episodes(ts);
CREATE INDEX idx_episodes_pending ON episodes(processed) WHERE processed = 0;
CREATE INDEX idx_episodes_open ON episodes(held_close) WHERE held_close = 0;

CREATE TABLE entities (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    display_name TEXT NOT NULL,
    tombstoned   INTEGER NOT NULL DEFAULT 0,
    merged_into  TEXT REFERENCES entities(id),
    created_ts   TEXT NOT NULL
);
CREATE INDEX idx_entities_name ON entities(display_name);

CREATE TABLE entity_aliases (
    entity_id TEXT NOT NULL REFERENCES entities(id),
    alias     TEXT NOT NULL,
    added_ts  TEXT NOT NULL,
    PRIMARY KEY (entity_id, alias)
);
CREATE INDEX idx_aliases_alias ON entity_aliases(alias);

CREATE TABLE facts (
    id             TEXT PRIMARY KEY,
    subject_entity TEXT NOT NULL REFERENCES entities(id),
    predicate      TEXT NOT NULL,
    object_kind    TEXT NOT NULL CHECK (object_kind IN ('entity','literal','date')),
    object_value   TEXT NOT NULL,
    epistemic      TEXT NOT NULL DEFAULT 'asserted',
    origin         TEXT NOT NULL,
    locked         INTEGER NOT NULL DEFAULT 0,
    tombstoned     INTEGER NOT NULL DEFAULT 0,
    confidence     REAL NOT NULL DEFAULT 1.0,
    valid_from     TEXT NOT NULL,
    superseded_by  TEXT REFERENCES facts(id),
    promoted_from  TEXT,
    approved_by_elliot INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_facts_subject ON facts(subject_entity);
CREATE INDEX idx_facts_current ON facts(subject_entity, predicate)
    WHERE superseded_by IS NULL AND tombstoned = 0;

CREATE TABLE impressions (
    id            TEXT PRIMARY KEY,
    ts_formed     TEXT NOT NULL,
    statement     TEXT NOT NULL,
    support_count INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','faded','contradicted','promoted')),
    superseded_by TEXT REFERENCES impressions(id)
);

CREATE TABLE impression_support (
    impression_id TEXT NOT NULL REFERENCES impressions(id),
    episode_id    TEXT NOT NULL REFERENCES episodes(id),
    PRIMARY KEY (impression_id, episode_id)
);

CREATE TABLE gaps (
    id           TEXT PRIMARY KEY,
    about_entity TEXT REFERENCES entities(id),
    description  TEXT NOT NULL,
    spawned_from TEXT,
    status       TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','answered','stale')),
    answered_by  TEXT,
    created_ts   TEXT NOT NULL
);
CREATE INDEX idx_gaps_open ON gaps(status) WHERE status = 'open';

CREATE TABLE provenance (
    claim_id   TEXT NOT NULL,
    episode_id TEXT NOT NULL REFERENCES episodes(id),
    PRIMARY KEY (claim_id, episode_id)
);

CREATE TABLE promotion_queue (
    id            TEXT PRIMARY KEY,
    impression_id TEXT REFERENCES impressions(id),
    proposed_fact TEXT NOT NULL,          -- JSON of the fact-to-be
    nominated_ts  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected')),
    decided_ts    TEXT
);

CREATE TABLE waiting_message (
    id         INTEGER PRIMARY KEY CHECK (id = 1),   -- one pending max (§3.4)
    content    TEXT NOT NULL,
    thread_ref TEXT,
    written_ts TEXT NOT NULL,
    revised_ts TEXT,
    surfaced   INTEGER NOT NULL DEFAULT 0,
    read_ts    TEXT
);

CREATE TABLE audit_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_id     TEXT NOT NULL,
    detail     TEXT
);
"""

_INTERIOR_V1 = """
CREATE TABLE episodes (
    id         TEXT PRIMARY KEY,
    ts         TEXT NOT NULL,
    source     TEXT NOT NULL,        -- reflection | finding | reading
    content    TEXT NOT NULL,
    processed  INTEGER NOT NULL DEFAULT 0,
    source_key TEXT UNIQUE
);
CREATE INDEX idx_int_episodes_ts ON episodes(ts);
CREATE INDEX idx_int_episodes_pending ON episodes(processed) WHERE processed = 0;

CREATE TABLE stance_events (
    id        TEXT PRIMARY KEY,
    ts        TEXT NOT NULL,
    topic     TEXT NOT NULL,
    source    TEXT,                  -- url / episode ref / 'elliot-argument'
    direction TEXT NOT NULL,
    why       TEXT NOT NULL,
    origin    TEXT NOT NULL DEFAULT 'she'   -- she | elliot-argument | elliot-override
);
CREATE INDEX idx_stance_topic ON stance_events(topic);

CREATE TABLE beliefs (
    id            TEXT PRIMARY KEY,
    topic         TEXT NOT NULL UNIQUE,
    direction     TEXT NOT NULL,
    weight        REAL NOT NULL DEFAULT 0.0,
    hardened      INTEGER NOT NULL DEFAULT 0,
    steelman_done INTEGER NOT NULL DEFAULT 0,   -- gate: no hardening without it
    updated_ts    TEXT NOT NULL
);

CREATE TABLE belief_history (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    belief_id TEXT NOT NULL REFERENCES beliefs(id),
    ts        TEXT NOT NULL,
    change    TEXT NOT NULL,          -- JSON: old/new direction+weight, cause
    origin    TEXT NOT NULL           -- she | elliot-argument | elliot-override (scar, visible)
);

CREATE TABLE source_trust (
    domain     TEXT PRIMARY KEY,
    substance  INTEGER NOT NULL DEFAULT 0,
    slop       INTEGER NOT NULL DEFAULT 0,
    burned     INTEGER NOT NULL DEFAULT 0,
    updated_ts TEXT NOT NULL
);

CREATE TABLE audit_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_id     TEXT NOT NULL,
    detail     TEXT
);
"""

_MIGRATIONS: dict[str, list[str]] = {
    "relational": [_RELATIONAL_V1],
    "interior": [_INTERIOR_V1],
}


def ensure_schema(store: str) -> None:
    """Apply pending migrations. Idempotent; safe to call at boot."""
    conn = _connect(store)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        pending = _MIGRATIONS[store][version:]
        for i, ddl in enumerate(pending, start=version + 1):
            with conn:
                conn.executescript(ddl)
                conn.execute(f"PRAGMA user_version={i}")
            log("sqlite_core", "migrated", store=store, version=i)
    finally:
        conn.close()


# ── single-writer queue ───────────────────────────────────────────────────
class _Writer:
    """Owns the sole write connection for one store.

    submit() runs a mutation function on the writer's thread-safe queue; the
    function receives an open connection inside a transaction and MUST write
    its own audit row(s). Commit on return, rollback on raise.
    """

    def __init__(self, store: str):
        self.store = store
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            ensure_schema(self.store)
            self._conn = _connect(self.store)
        return self._conn

    async def submit(self, mutate, *, actor: str, action: str) -> Any:
        """Run `mutate(conn, audit)` atomically. Serialized per store."""
        async with self._lock:
            conn = self._get_conn()

            def audit(table_name: str, row_id: str, detail: dict | None = None,
                      *, _action: str | None = None) -> None:
                conn.execute(
                    "INSERT INTO audit_log (ts, actor, action, table_name, row_id, detail) "
                    "VALUES (?,?,?,?,?,?)",
                    (now_utc(), actor, _action or action, table_name, row_id,
                     json.dumps(detail, ensure_ascii=False) if detail else None),
                )

            def _run():
                try:
                    with conn:  # transaction: commit/rollback
                        return mutate(conn, audit)
                except Exception:
                    raise

            return await asyncio.to_thread(_run)


_writers: dict[str, _Writer] = {}


def writer(store: str) -> _Writer:
    if store not in _writers:
        _writers[store] = _Writer(store)
    return _writers[store]


# ── read helpers (graceful degradation) ───────────────────────────────────
def query(store: str, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
    """Read-only query. Returns [] on ANY failure — never raises into callers
    on the chat/heartbeat path (Invariant 1)."""
    try:
        conn = _connect(store, readonly=True)
        try:
            return conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        warning(f"sqlite_core/query[{store}]: {type(exc).__name__}: {exc}")
        return []


def backup(store: str, dest_dir: Path, keep: int = 7) -> Path | None:
    """Online .backup + rotation. Called from the consolidation quiet slot."""
    try:
        if not _DB_PATHS[store].exists():
            return None  # store not created yet — nothing to back up
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = dest_dir / f"{store}-{stamp}.db"
        src = _connect(store, readonly=True)
        try:
            dst = sqlite3.connect(dest)
            with dst:
                src.backup(dst)
            dst.close()
        finally:
            src.close()
        old = sorted(dest_dir.glob(f"{store}-*.db"))[:-keep]
        for p in old:
            p.unlink(missing_ok=True)
        return dest
    except Exception as exc:
        warning(f"sqlite_core/backup[{store}]: {type(exc).__name__}: {exc}")
        return None


# ── offline self-test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile

    async def _main():
        global _DB_PATHS, _writers
        d = Path(tempfile.mkdtemp(prefix="sqlite_core_test_"))
        _DB_PATHS = {s: d / f"{s}.db" for s in STORES}
        _writers = {}

        # 1. schema applies + is idempotent
        ensure_schema("relational")
        ensure_schema("relational")
        ensure_schema("interior")

        # 2. contamination wall: relational conn cannot see interior tables
        conn = _connect("relational", readonly=True)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "beliefs" not in tables, "WALL BREACH: beliefs visible in relational"
        assert "facts" in tables

        # 3. write + audit in one transaction
        w = writer("relational")

        def _ins(conn, audit):
            eid = new_id()
            conn.execute(
                "INSERT INTO episodes (id, ts, source, speaker, content) VALUES (?,?,?,?,?)",
                (eid, now_utc(), "conversation", "elliot", "test episode"))
            audit("episodes", eid, {"content": "test episode"})
            return eid

        eid = await w.submit(_ins, actor="she", action="insert")
        rows = query("relational", "SELECT * FROM episodes WHERE id=?", (eid,))
        assert len(rows) == 1
        arows = query("relational", "SELECT * FROM audit_log")
        assert len(arows) == 1 and arows[0]["row_id"] == eid

        # 4. rollback on failure: nothing persists, audit included
        def _boom(conn, audit):
            conn.execute(
                "INSERT INTO episodes (id, ts, source, speaker, content) VALUES (?,?,?,?,?)",
                (new_id(), now_utc(), "conversation", "elliot", "doomed"))
            audit("episodes", "doomed", None)
            raise RuntimeError("boom")

        try:
            await w.submit(_boom, actor="she", action="insert")
            assert False, "should have raised"
        except RuntimeError:
            pass
        assert len(query("relational", "SELECT * FROM episodes")) == 1
        assert len(query("relational", "SELECT * FROM audit_log")) == 1

        # 5. waiting_message singleton constraint
        def _wm(conn, audit):
            conn.execute(
                "INSERT INTO waiting_message (id, content, written_ts) VALUES (1,?,?)",
                ("hello", now_utc()))
            audit("waiting_message", "1", None)

        await w.submit(_wm, actor="she", action="insert")
        try:
            def _wm2(conn, audit):
                conn.execute(
                    "INSERT INTO waiting_message (id, content, written_ts) VALUES (2,?,?)",
                    ("second", now_utc()))
            await w.submit(_wm2, actor="she", action="insert")
            assert False, "singleton CHECK should reject id=2"
        except sqlite3.IntegrityError:
            pass

        # 6. read helper degrades to [] on bad SQL, never raises
        assert query("relational", "SELECT * FROM nonexistent") == []

        # 7. UTC timestamps are tz-aware
        ts = query("relational", "SELECT ts FROM episodes")[0]["ts"]
        assert "+00:00" in ts or ts.endswith("Z"), f"naive timestamp: {ts}"

        # 8. backup + rotation
        bdir = d / "bak"
        p1 = backup("relational", bdir, keep=1)
        p2 = backup("relational", bdir, keep=1)
        assert p2 is not None and p2.exists()
        assert len(list(bdir.glob("relational-*.db"))) == 1, "rotation failed"

        print("OK sqlite_core self-test")

    asyncio.run(_main())
