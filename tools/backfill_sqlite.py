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

from database import Database, relational_db, interior_db


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


def _read_sensitive(record: dict) -> object:
    """Read sensitive flag, tolerating old `held_close` key."""
    return record["sensitive"] if "sensitive" in record else record.get("held_close")


def backfill_relational(db: Database, data_root: Path) -> dict[str, int]:
    """Backfill relational.db from JSONL files. Returns table->row_count."""
    events_path = data_root / "events.jsonl"
    entities_path = data_root / "relational" / "entities.jsonl"
    heartbeat_path = data_root / "relational" / "heartbeat.jsonl"
    embeddings_path = data_root / "relational" / "embeddings.jsonl"

    counts: dict[str, int] = {}

    # --- events + privacy + chat_boundaries from events.jsonl ---
    records = _read_jsonl(events_path)

    events = []
    privacy = []
    boundaries = []

    for r in records:
        kind = r.get("kind")
        if kind == "privacy":
            sens = _read_sensitive(r)
            if not isinstance(sens, bool):
                continue
            privacy.append((
                r["target_id"],
                int(sens),
                r["source"],
                r.get("carry_after"),
                r["said_at"],
            ))
        elif kind == "chat_boundary":
            boundaries.append((r["said_at"],))
        elif r.get("role") in ("user", "assistant"):
            sens_val = _read_sensitive(r)
            pe = r.get("provider_excluded")
            pca = r.get("privacy_carry_after")
            events.append((
                r.get("id", f"legacy:{len(events)}"),
                r["role"],
                r["content"],
                r["said_at"],
                int(sens_val) if isinstance(sens_val, bool) else None,
                int(pe) if isinstance(pe, bool) else None,
                pca,
            ))

    if events:
        db.executemany(
            "INSERT OR IGNORE INTO events (id, role, content, said_at, sensitive, provider_excluded, privacy_carry_after) VALUES (?, ?, ?, ?, ?, ?, ?)",
            events,
        )
    counts["events"] = db.count("events")

    if privacy:
        db.executemany(
            "INSERT OR IGNORE INTO privacy_records (target_id, sensitive, source, carry_after, said_at) VALUES (?, ?, ?, ?, ?)",
            privacy,
        )
    counts["privacy_records"] = db.count("privacy_records")

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
        db.execute(
            "INSERT OR IGNORE INTO heartbeat_completions (stage, source_event_id, said_at) VALUES (?, ?, ?)",
            (r["stage"], r["source_event_id"], r["said_at"]),
        )
    counts["heartbeat_completions"] = db.count("heartbeat_completions")

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
            "INSERT OR IGNORE INTO identity_entries (id, kind, claim, evidence, target_id, verdict, said_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (r["id"], r.get("kind"), r.get("claim"),
             json.dumps(r["evidence"]) if isinstance(r.get("evidence"), list) else None,
             r.get("target_id"), r.get("verdict"), r.get("said_at")),
        )
    counts["identity_entries"] = db.count("identity_entries")

    # --- waiting_message (single-row table) ---
    if waiting_path.exists():
        try:
            msg = json.loads(waiting_path.read_text(encoding="utf-8"))
            if isinstance(msg, dict) and "content" in msg:
                db.execute(
                    "INSERT OR REPLACE INTO waiting_message (id, content, said_at, revised_at, read) VALUES (1, ?, ?, ?, ?)",
                    (msg["content"], msg["said_at"], msg.get("revised_at"), int(msg.get("read", False))),
                )
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    counts["waiting_message"] = db.count("waiting_message")

    return counts


def verify(rel_counts: dict[str, int], int_counts: dict[str, int], data_root: Path) -> list[str]:
    """Compare SQLite row counts against JSONL line counts. Returns list of mismatches."""
    mismatches: list[str] = []
    events_path = data_root / "events.jsonl"

    if events_path.exists():
        records = _read_jsonl(events_path)
        expected_events = sum(1 for r in records if r.get("role") in ("user", "assistant"))
        expected_privacy = sum(1 for r in records if r.get("kind") == "privacy" and isinstance(_read_sensitive(r), bool))
        expected_boundaries = sum(1 for r in records if r.get("kind") == "chat_boundary")

        if rel_counts.get("events", 0) != expected_events:
            mismatches.append(f"events: expected {expected_events}, got {rel_counts.get('events', 0)}")
        if rel_counts.get("privacy_records", 0) != expected_privacy:
            mismatches.append(f"privacy_records: expected {expected_privacy}, got {rel_counts.get('privacy_records', 0)}")
        if rel_counts.get("chat_boundaries", 0) != expected_boundaries:
            mismatches.append(f"chat_boundaries: expected {expected_boundaries}, got {rel_counts.get('chat_boundaries', 0)}")

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
