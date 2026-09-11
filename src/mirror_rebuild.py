"""Backfill SQLite mirrors from JSONL source-of-truth files.

Idempotent: uses INSERT OR IGNORE with UNIQUE constraints, so running
twice produces the same row counts. Safe to run while Sage is live —
WAL mode allows concurrent reads.

Usage:
    python3 tools/backfill_sqlite.py [--data-root ~/sage_data] [--verify]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from database import Database, relational_db, interior_db  # noqa: E402
from events import EventStore, legacy_session_id  # noqa: E402
from persistence import guarded, read_jsonl


def _read_jsonl(path: Path) -> list[dict]:
    return [record for record in read_jsonl(path) if isinstance(record, dict)]


@guarded(lambda db, data_root: data_root)
def backfill_relational(db: Database, data_root: Path) -> dict[str, int]:
    """Backfill relational.db from JSONL files. Returns table->row_count."""
    events_path = data_root / "events.jsonl"
    entities_path = data_root / "relational" / "entities.jsonl"
    heartbeat_path = data_root / "relational" / "heartbeat.jsonl"
    embeddings_path = data_root / "relational" / "embeddings.jsonl"
    searches_path = data_root / "relational" / "searches.jsonl"

    counts: dict[str, int] = {}

    # --- events + chat_boundaries from events.jsonl ---
    records = EventStore(data_root)._read_records()

    events = []
    sessions: dict[str, tuple[str, str]] = {}
    session_titles: dict[str, str] = {}
    session_archived: dict[str, bool] = {}
    session_models: dict[str, str] = {}
    session_voice_models: dict[str, str] = {}
    event_sessions = []
    event_sources = []
    voice_event_context = []
    boundaries = []
    transcript_corrections = []

    session_id = legacy_session_id(-1)
    for index, r in enumerate(records):
        kind = r.get("kind")
        if kind == "chat_boundary":
            boundaries.append((r["said_at"],))
            session_id = r.get("session_id") if isinstance(r.get("session_id"), str) else legacy_session_id(index)
            sessions.setdefault(session_id, (r["said_at"], r["said_at"]))
        elif kind == "transcript_correction":
            transcript_corrections.append((r["id"], r["source_event_id"], r["content"], r["said_at"]))
        elif kind == "session_metadata" and isinstance(r.get("session_id"), str):
            if isinstance(r.get("title"), str):
                session_titles[r["session_id"]] = r["title"]
            if isinstance(r.get("archived"), bool):
                session_archived[r["session_id"]] = r["archived"]
            if isinstance(r.get("model"), str):
                session_models[r["session_id"]] = r["model"]
            if isinstance(r.get("voice_model"), str):
                session_voice_models[r["session_id"]] = r["voice_model"]
        elif r.get("role") in ("user", "assistant"):
            event_id = r.get("id", f"legacy:{index}")
            event_session_id = r.get("session_id") if isinstance(r.get("session_id"), str) else session_id
            events.append((
                event_id,
                r["role"],
                r["content"],
                r["said_at"],
                r.get("model"),
            ))
            created_at, last_active_at = sessions.get(event_session_id, (r["said_at"], r["said_at"]))
            sessions[event_session_id] = (
                min(created_at, r["said_at"]),
                max(last_active_at, r["said_at"]),
            )
            event_sessions.append((event_id, event_session_id))
            if r.get("source") in {"text", "voice"}:
                event_sources.append((event_id, r["source"]))
            if isinstance(r.get("call_id"), str) and isinstance(r.get("turn_id"), str):
                voice_event_context.append((event_id, r["call_id"], r["turn_id"]))

    if events:
        db.executemany(
            "INSERT OR IGNORE INTO events (id, role, content, said_at, model) VALUES (?, ?, ?, ?, ?)",
            events,
        )
    counts["events"] = db.count("events")

    if sessions:
        db.executemany(
            "INSERT INTO sessions (id, created_at, last_active_at, title, archived, model, voice_model) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "created_at = MIN(created_at, excluded.created_at), "
            "last_active_at = MAX(last_active_at, excluded.last_active_at), "
            "title = excluded.title, archived = excluded.archived, model = excluded.model, voice_model = excluded.voice_model",
            [
                (session, *timestamps, session_titles.get(session), int(session_archived.get(session, False)), session_models.get(session, "auto"), session_voice_models.get(session, "same"))
                for session, timestamps in sessions.items()
            ],
        )
    counts["sessions"] = db.count("sessions")

    if event_sessions:
        db.executemany(
            "INSERT OR IGNORE INTO event_sessions (event_id, session_id) VALUES (?, ?)",
            event_sessions,
        )
    counts["event_sessions"] = db.count("event_sessions")

    if event_sources:
        db.executemany(
            "INSERT OR IGNORE INTO event_sources (event_id, source) VALUES (?, ?)",
            event_sources,
        )
    counts["event_sources"] = db.count("event_sources")

    if voice_event_context:
        db.executemany(
            "INSERT OR IGNORE INTO voice_event_context (event_id, call_id, turn_id) VALUES (?, ?, ?)",
            voice_event_context,
        )
    counts["voice_event_context"] = db.count("voice_event_context")

    if transcript_corrections:
        db.executemany(
            "INSERT OR IGNORE INTO transcript_corrections (id, source_event_id, content, said_at) VALUES (?, ?, ?, ?)",
            transcript_corrections,
        )
    counts["transcript_corrections"] = db.count("transcript_corrections")

    if boundaries:
        db.executemany(
            "INSERT OR IGNORE INTO chat_boundaries (said_at) VALUES (?)",
            boundaries,
        )
    counts["chat_boundaries"] = db.count("chat_boundaries")

    # --- entity_observations ---
    for r in _read_jsonl(entities_path):
        if r.get("kind") != "entity_obs":
            continue
        db.execute(
            "INSERT OR IGNORE INTO entity_observations (entity_id, name, observation, said_at, source_event_id) VALUES (?, ?, ?, ?, ?)",
            (r["entity_id"], r["name"], r["observation"], r["said_at"], r.get("source_event_id")),
        )
    counts["entity_observations"] = db.count("entity_observations")

    # --- heartbeat_completions ---
    for r in _read_jsonl(heartbeat_path):
        if r.get("kind") != "heartbeat":
            continue
        if r.get("stage") == "metabolism":
            db.execute(
                "INSERT OR IGNORE INTO metabolism_completions (source_event_id, said_at) VALUES (?, ?)",
                (r["source_event_id"], r["said_at"]),
            )
        elif r.get("stage") in {"entities", "reflection"}:
            db.execute(
                "INSERT OR IGNORE INTO heartbeat_completions (stage, source_event_id, said_at) VALUES (?, ?, ?)",
                (r["stage"], r["source_event_id"], r["said_at"]),
            )
    counts["heartbeat_completions"] = db.count("heartbeat_completions") + db.count("metabolism_completions")

    # --- search_records ---
    for r in _read_jsonl(searches_path):
        if r.get("kind") != "search":
            continue
        db.execute(
            "INSERT OR IGNORE INTO search_records (id, query, sources, origin, source_event_id, said_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                r["id"],
                r["query"],
                json.dumps(r["sources"], ensure_ascii=False),
                r["origin"],
                r["source_event_id"],
                r["said_at"],
            ),
        )
    counts["search_records"] = db.count("search_records")

    # --- embeddings ---
    for r in _read_jsonl(embeddings_path):
        if "event_id" not in r or "vector" not in r:
            continue
        db.execute(
            "INSERT OR REPLACE INTO embeddings (event_id, vector) VALUES (?, ?)",
            (r["event_id"], json.dumps(r["vector"])),
        )
    counts["embeddings"] = db.count("embeddings")

    return counts


@guarded(lambda db, data_root: data_root)
def backfill_interior(db: Database, data_root: Path) -> dict[str, int]:
    """Backfill interior.db from JSONL/JSON files. Returns table->row_count."""
    reflections_path = data_root / "interior" / "reflections.jsonl"
    waiting_path = data_root / "interior" / "waiting_message.json"
    identity_path = data_root / "interior" / "identity.jsonl"

    counts: dict[str, int] = {}

    # --- reflections ---
    for r in _read_jsonl(reflections_path):
        db.execute(
            "INSERT OR IGNORE INTO reflections (id, content, said_at, category, source_event_id) VALUES (?, ?, ?, ?, ?)",
            (r["id"], r["content"], r["said_at"], r.get("category", "general"), r.get("source_event_id")),
        )
    counts["reflections"] = db.count("reflections")

    # --- identity proposals and rulings (one table, folded at read time) ---
    for r in _read_jsonl(identity_path):
        if not r.get("id"):
            continue
        db.execute(
            "INSERT OR IGNORE INTO identity_entries (id, kind, claim, evidence, target_id, verdict, said_at, source_event_ids)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (r["id"], r.get("kind"), r.get("claim"),
             json.dumps(r["evidence"]) if isinstance(r.get("evidence"), list) else None,
             r.get("target_id"), r.get("verdict"), r.get("said_at"),
             json.dumps(r.get("source_event_ids", [])) if r.get("source_event_ids") else None),
        )
    counts["identity_entries"] = db.count("identity_entries")

    # --- waiting_message (single-row table) ---
    if waiting_path.exists():
        try:
            msg = json.loads(waiting_path.read_text(encoding="utf-8"))
            if isinstance(msg, dict) and "content" in msg:
                db.execute(
                    "INSERT OR REPLACE INTO waiting_message (id, content, said_at, revised_at, read, source_event_id) VALUES (1, ?, ?, ?, ?, ?)",
                    (msg["content"], msg["said_at"], msg.get("revised_at"), int(msg.get("read", False)), msg.get("source_event_id")),
                )
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    counts["waiting_message"] = db.count("waiting_message")

    return counts


_SURROGATE_IDS = {"entity_observations", "heartbeat_completions", "chat_boundaries"}
_JSON_COLUMNS = {
    ("search_records", "sources"),
    ("embeddings", "vector"),
    ("identity_entries", "evidence"),
    ("identity_entries", "source_event_ids"),
}


def _database_snapshot(path: Path) -> dict[str, list[tuple]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            if not row[0].startswith("sqlite_")
        ]
        snapshot: dict[str, list[tuple]] = {}
        for table in tables:
            quoted_table = '"' + table.replace('"', '""') + '"'
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({quoted_table})")]
            if table in _SURROGATE_IDS:
                columns.remove("id")
            quoted_columns = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
            rows = []
            for row in connection.execute(f"SELECT {quoted_columns} FROM {quoted_table}"):
                normalized = []
                for column, value in zip(columns, row):
                    if (table, column) in _JSON_COLUMNS and isinstance(value, str):
                        try:
                            value = json.dumps(json.loads(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                        except json.JSONDecodeError:
                            pass
                    normalized.append(value)
                rows.append(tuple(normalized))
            snapshot[table] = sorted(rows, key=repr)
        return snapshot
    finally:
        connection.close()


def _compare_mirror(kind: str, data_root: Path) -> list[str]:
    actual_path = data_root / kind / f"{kind}.db"
    if not actual_path.exists():
        return [f"{kind}: database missing"]
    with TemporaryDirectory() as temporary_directory:
        expected_root = Path(temporary_directory)
        expected_db = relational_db(expected_root) if kind == "relational" else interior_db(expected_root)
        if kind == "relational":
            backfill_relational(expected_db, data_root)
        else:
            backfill_interior(expected_db, data_root)
        expected_path = expected_db.db_path
        expected_db.close()
        expected = _database_snapshot(expected_path)
    actual = _database_snapshot(actual_path)
    return [
        f"{kind}.{table}: content differs"
        for table, rows in expected.items()
        if actual.get(table) != rows
    ]


@guarded(lambda rel_counts, int_counts, data_root: data_root)
def verify(rel_counts: dict[str, int], int_counts: dict[str, int], data_root: Path) -> list[str]:
    """Compare complete SQLite mirror contents with fresh JSONL-derived mirrors."""
    mismatches: list[str] = []
    if rel_counts:
        mismatches.extend(_compare_mirror("relational", data_root))
    if int_counts:
        mismatches.extend(_compare_mirror("interior", data_root))
    return mismatches


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill SQLite mirrors from JSONL.")
    parser.add_argument("--data-root", type=Path, default=Path.home() / "sage_data")
    parser.add_argument("--verify", action="store_true", help="Verify mirror contents match JSONL")
    args = parser.parse_args()

    data_root = args.data_root
    if not data_root.exists():
        print(f"Data root {data_root} not found.")
        sys.exit(1)

    rel = relational_db(data_root)
    intr = interior_db(data_root)

    print("Backfilling relational mirror...")
    rel_counts = backfill_relational(rel, data_root)
    for table, count in sorted(rel_counts.items()):
        print(f"  {table}: {count} rows")

    print("Backfilling interior mirror...")
    int_counts = backfill_interior(intr, data_root)
    for table, count in sorted(int_counts.items()):
        print(f"  {table}: {count} rows")

    if args.verify:
        print("Verifying...")
        mismatches = verify(rel_counts, int_counts, data_root)
        if mismatches:
            print("MISMATCHES:")
            for m in mismatches:
                print(f"  {m}")
            sys.exit(1)
        else:
            print("All mirror contents match.")

    rel.close()
    intr.close()
    print("Done.")


if __name__ == "__main__":
    main()
