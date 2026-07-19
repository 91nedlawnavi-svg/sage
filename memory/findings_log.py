import json
import os
from pathlib import Path
from datetime import datetime
from config.settings import FINDINGS_PATH
from utils.logger import warning


def _ensure_parent_dir():
    """Ensure the parent directory for findings exists."""
    FINDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)


def append_finding(query: str, results: list[dict], source: str = "search") -> dict:
    """Append a finding entry to the JSONL log. Returns the entry written
    (the SQLite intake mirror reuses its ts as the dedup source_key).

    source: "autonomous" (heartbeat, counts against the daily budget) or
    "search" (Elliot's /search command, budget-exempt).
    """
    _ensure_parent_dir()
    entry = {
        "ts": datetime.now().isoformat(),
        "query": query,
        "results": results,
        "source": source,
    }
    # True append (O_APPEND): one JSONL line per call. The old read-all/
    # rewrite-all pattern raced concurrent writers (heartbeat + /search) and
    # dropped findings — blueprint Wave 1 #4, "the findings race".
    try:
        with open(FINDINGS_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        # never raise into the heartbeat/chat path — but never silently either
        warning(f"findings_log/append failed: {e}")
    return entry


def read_recent(n: int = 20) -> list[dict]:
    """Read the most recent N findings."""
    if not FINDINGS_PATH.exists():
        return []
    entries = []
    try:
        with open(FINDINGS_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        return []
    return entries[-n:]