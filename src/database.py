"""SQLite storage backend for Sage.

Replaces JSONL file storage with a single SQLite database that can scale
to lifelong episodic memory. The database lives at ~/sage_data/sage.db
and contains all event, privacy, entity, heartbeat, embedding, reflection,
belief, and waiting-message records.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    said_at TEXT NOT NULL,
    held_close INTEGER NOT NULL DEFAULT 0,
    provider_excluded INTEGER NOT NULL DEFAULT 0,
    privacy_carry_after INTEGER
);

CREATE INDEX IF NOT EXISTS idx_events_said_at ON events(said_at);
CREATE INDEX IF NOT EXISTS idx_events_role ON events(role);

CREATE TABLE IF NOT EXISTS privacy_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id TEXT NOT NULL,
    held_close INTEGER NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('sensor', 'user')),
    carry_after INTEGER,
    said_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_privacy_target ON privacy_records(target_id);

CREATE TABLE IF NOT EXISTS entity_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,
    name TEXT NOT NULL,
    observation TEXT NOT NULL,
    said_at TEXT NOT NULL,
    source_event_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_entity_obs_entity ON entity_observations(entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_obs_source ON entity_observations(source_event_id);

CREATE TABLE IF NOT EXISTS heartbeat_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL CHECK(stage IN ('entities', 'reflection')),
    source_event_id TEXT NOT NULL,
    said_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_boundaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    said_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    event_id TEXT PRIMARY KEY,
    vector TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reflections (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    said_at TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    source_event_id TEXT
);

CREATE TABLE IF NOT EXISTS beliefs (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    stance TEXT NOT NULL,
    evidence TEXT NOT NULL,
    said_at TEXT NOT NULL,
    revised_from TEXT
);

CREATE TABLE IF NOT EXISTS waiting_message (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    content TEXT NOT NULL,
    said_at TEXT NOT NULL,
    revised_at TEXT,
    read INTEGER NOT NULL DEFAULT 0
);
"""


class Database:
    """Thin wrapper around a SQLite connection for Sage storage."""

    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = data_root or Path.home() / "sage_data"
        self.db_path = self.data_root / "sage.db"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False,
                isolation_level=None,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple[Any, ...]]) -> sqlite3.Cursor:
        return self.conn.executemany(sql, params_list)

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        cursor = self.conn.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(zip([desc[0] for desc in cursor.description], row))

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = self.conn.execute(sql, params)
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def store_embedding_vector(self, event_id: str, vector: list[float]) -> None:
        serialized = json.dumps(vector)
        self.execute(
            "INSERT OR REPLACE INTO embeddings (event_id, vector) VALUES (?, ?)",
            (event_id, serialized),
        )

    def load_embedding_vectors(self) -> dict[str, list[float]]:
        rows = self.fetchall("SELECT event_id, vector FROM embeddings")
        result: dict[str, list[float]] = {}
        for row in rows:
            try:
                result[row["event_id"]] = json.loads(row["vector"])
            except (json.JSONDecodeError, TypeError):
                continue
        return result
