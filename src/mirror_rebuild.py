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
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from database import Database, relational_db, interior_db  # noqa: E402
from events import EventStore, legacy_session_id  # noqa: E402
from persistence import guarded


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                records.append(obj)
        except json.JSONDecodeError:
            # tolerate truncated last line (crash mid-write)
            if i == len(path.read_text(encoding="utf-8").splitlines()) - 1:
                break
            raise
    return records


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


@guarded(lambda rel_counts, int_counts, data_root: data_root)
def verify(rel_counts: dict[str, int], int_counts: dict[str, int], data_root: Path) -> list[str]:
    """Compare SQLite row counts against JSONL line counts. Returns list of mismatches."""
    mismatches: list[str] = []
    events_path = data_root / "events.jsonl"

    if events_path.exists():
        records = EventStore(data_root)._read_records()
        expected_events = sum(1 for r in records if r.get("role") in ("user", "assistant"))
        expected_boundaries = sum(1 for r in records if r.get("kind") == "chat_boundary")
        expected_sources = sum(
            1 for r in records
            if r.get("role") in ("user", "assistant") and r.get("source") in {"text", "voice"}
        )
        expected_corrections = sum(1 for r in records if r.get("kind") == "transcript_correction")
        expected_voice_context = sum(
            1 for r in records
            if r.get("role") in ("user", "assistant")
            and isinstance(r.get("call_id"), str)
            and isinstance(r.get("turn_id"), str)
        )
        expected_sessions: set[str] = set()
        expected_event_sessions = 0
        session_id = legacy_session_id(-1)
        for index, record in enumerate(records):
            if record.get("kind") == "chat_boundary":
                session_id = record.get("session_id") if isinstance(record.get("session_id"), str) else legacy_session_id(index)
                expected_sessions.add(session_id)
            elif record.get("role") in ("user", "assistant"):
                expected_sessions.add(record.get("session_id") if isinstance(record.get("session_id"), str) else session_id)
                expected_event_sessions += 1

        if rel_counts.get("events", 0) != expected_events:
            mismatches.append(f"events: expected {expected_events}, got {rel_counts.get('events', 0)}")
        if rel_counts.get("sessions", 0) != len(expected_sessions):
            mismatches.append(f"sessions: expected {len(expected_sessions)}, got {rel_counts.get('sessions', 0)}")
        if rel_counts.get("event_sessions", 0) != expected_event_sessions:
            mismatches.append(
                f"event_sessions: expected {expected_event_sessions}, got {rel_counts.get('event_sessions', 0)}"
            )
        if rel_counts.get("chat_boundaries", 0) != expected_boundaries:
            mismatches.append(f"chat_boundaries: expected {expected_boundaries}, got {rel_counts.get('chat_boundaries', 0)}")
        if rel_counts.get("event_sources", 0) != expected_sources:
            mismatches.append(f"event_sources: expected {expected_sources}, got {rel_counts.get('event_sources', 0)}")
        if rel_counts.get("transcript_corrections", 0) != expected_corrections:
            mismatches.append(
                f"transcript_corrections: expected {expected_corrections}, got {rel_counts.get('transcript_corrections', 0)}"
            )
        if rel_counts.get("voice_event_context", 0) != expected_voice_context:
            mismatches.append(
                f"voice_event_context: expected {expected_voice_context}, got {rel_counts.get('voice_event_context', 0)}"
            )

    entities_path = data_root / "relational" / "entities.jsonl"
    if entities_path.exists():
        expected = sum(1 for r in _read_jsonl(entities_path) if r.get("kind") == "entity_obs")
        if rel_counts.get("entity_observations", 0) != expected:
            mismatches.append(f"entity_observations: expected {expected}, got {rel_counts.get('entity_observations', 0)}")

    heartbeat_path = data_root / "relational" / "heartbeat.jsonl"
    if heartbeat_path.exists():
        expected = sum(1 for r in _read_jsonl(heartbeat_path) if r.get("kind") == "heartbeat")
        if rel_counts.get("heartbeat_completions", 0) != expected:
            mismatches.append(f"heartbeat_completions: expected {expected}, got {rel_counts.get('heartbeat_completions', 0)}")

    embeddings_path = data_root / "relational" / "embeddings.jsonl"
    if embeddings_path.exists():
        expected = sum(1 for r in _read_jsonl(embeddings_path) if "event_id" in r and "vector" in r)
        if rel_counts.get("embeddings", 0) != expected:
            mismatches.append(f"embeddings: expected {expected}, got {rel_counts.get('embeddings', 0)}")

    searches_path = data_root / "relational" / "searches.jsonl"
    if searches_path.exists():
        expected = sum(1 for r in _read_jsonl(searches_path) if r.get("kind") == "search")
        if rel_counts.get("search_records", 0) != expected:
            mismatches.append(f"search_records: expected {expected}, got {rel_counts.get('search_records', 0)}")

    reflections_path = data_root / "interior" / "reflections.jsonl"
    if reflections_path.exists():
        expected = len(_read_jsonl(reflections_path))
        if int_counts.get("reflections", 0) != expected:
            mismatches.append(f"reflections: expected {expected}, got {int_counts.get('reflections', 0)}")

    identity_path = data_root / "interior" / "identity.jsonl"
    if identity_path.exists():
        expected = sum(1 for r in _read_jsonl(identity_path) if r.get("id"))
        if int_counts.get("identity_entries", 0) != expected:
            mismatches.append(f"identity_entries: expected {expected}, got {int_counts.get('identity_entries', 0)}")

    return mismatches


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill SQLite mirrors from JSONL.")
    parser.add_argument("--data-root", type=Path, default=Path.home() / "sage_data")
    parser.add_argument("--verify", action="store_true", help="Verify row counts match JSONL")
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
            print("All counts match.")

    rel.close()
    intr.close()
    print("Done.")


if __name__ == "__main__":
    main()
