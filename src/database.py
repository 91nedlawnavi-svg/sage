"""SQLite mirrors of Sage's append-only stores.

JSONL under ``~/sage_data/`` remains the source of truth. These databases are
derived mirrors written alongside it, so a mirror can be rebuilt at any time
from the files by ``tools/backfill_sqlite.py`` without touching lived memory.

Relational and interior material live in separate database files, mirroring the
``relational/`` and ``interior/`` directory split required by INVARIANTS.md.

Events are mirrored as written. Sensitivity and provider exclusion stay derived
at read time from ``privacy_records``; they are not frozen into the events table.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


RELATIONAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    said_at TEXT NOT NULL,
    sensitive INTEGER,
    provider_excluded INTEGER,
    privacy_carry_after INTEGER
);

CREATE INDEX IF NOT EXISTS idx_events_said_at ON events(said_at);
CREATE INDEX IF NOT EXISTS idx_events_role ON events(role);

CREATE TABLE IF NOT EXISTS privacy_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id TEXT NOT NULL,
    sensitive INTEGER NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('sensor', 'user')),
    carry_after INTEGER,
    said_at TEXT NOT NULL,
    UNIQUE(target_id, source, said_at)
);

CREATE INDEX IF NOT EXISTS idx_privacy_target ON privacy_records(target_id);

CREATE TABLE IF NOT EXISTS entity_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,
    name TEXT NOT NULL,
    observation TEXT NOT NULL,
    said_at TEXT NOT NULL,
    source_event_id TEXT,
    UNIQUE(entity_id, source_event_id, said_at)
);

CREATE INDEX IF NOT EXISTS idx_entity_obs_entity ON entity_observations(entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_obs_source ON entity_observations(source_event_id);

CREATE TABLE IF NOT EXISTS heartbeat_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL CHECK(stage IN ('entities', 'reflection')),
    source_event_id TEXT NOT NULL,
    said_at TEXT NOT NULL,
    UNIQUE(stage, source_event_id)
);

CREATE TABLE IF NOT EXISTS chat_boundaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    said_at TEXT NOT NULL,
    UNIQUE(said_at)
);

CREATE TABLE IF NOT EXISTS embeddings (
    event_id TEXT PRIMARY KEY,
    vector TEXT NOT NULL
);
"""

INTERIOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS reflections (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    said_at TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    source_event_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_reflections_source ON reflections(source_event_id);

CREATE TABLE IF NOT EXISTS waiting_message (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    content TEXT NOT NULL,
    said_at TEXT NOT NULL,
    revised_at TEXT,
    read INTEGER NOT NULL DEFAULT 0
);
"""


class Database:
    """Thin wrapper around one SQLite file. Not a replacement for the JSONL stores."""

    def __init__(self, db_path: Path, schema: str) -> None:
        self.db_path = Path(db_path)
        self.schema = schema
        self._conn: sqlite3.Connection | None = None
        # ponytail: one lock per database file; split per table only if contention shows up
        self._lock = threading.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False,
                isolation_level=None,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(self.schema)
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple[Any, ...]]) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.executemany(sql, params_list)

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self._lock:
            cursor = self.conn.execute(sql, params)
            row = cursor.fetchone()
            if row is None:
                return None
            return dict(zip([desc[0] for desc in cursor.description], row))

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self.conn.execute(sql, params)
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def table_names(self) -> set[str]:
        return {row["name"] for row in self.fetchall("SELECT name FROM sqlite_master WHERE type='table'")}

    def count(self, table: str) -> int:
        row = self.fetchone(f"SELECT COUNT(*) AS n FROM {table}")
        return int(row["n"]) if row else 0

    def store_embedding_vector(self, event_id: str, vector: list[float]) -> None:
        self.execute(
            "INSERT OR REPLACE INTO embeddings (event_id, vector) VALUES (?, ?)",
            (event_id, json.dumps(vector)),
        )

    def load_embedding_vectors(self) -> dict[str, list[float]]:
        result: dict[str, list[float]] = {}
        for row in self.fetchall("SELECT event_id, vector FROM embeddings"):
            try:
                result[row["event_id"]] = json.loads(row["vector"])
            except (json.JSONDecodeError, TypeError):
                continue
        return result

    def embedding_vector(self, event_id: str) -> list[float] | None:
        row = self.fetchone("SELECT vector FROM embeddings WHERE event_id = ?", (event_id,))
        if row is None:
            return None
        try:
            return json.loads(row["vector"])
        except (json.JSONDecodeError, TypeError):
            return None


def relational_db(data_root: Path | None = None) -> Database:
    root = data_root or Path.home() / "sage_data"
    return Database(root / "relational" / "relational.db", RELATIONAL_SCHEMA)


def interior_db(data_root: Path | None = None) -> Database:
    root = data_root or Path.home() / "sage_data"
    return Database(root / "interior" / "interior.db", INTERIOR_SCHEMA)
