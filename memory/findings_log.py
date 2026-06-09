import json
import os
from pathlib import Path
from datetime import datetime
from config.settings import FINDINGS_PATH


def _ensure_parent_dir():
    """Ensure the parent directory for findings exists."""
    FINDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)


def append_finding(query: str, results: list[dict]) -> None:
    """Append a finding entry to the JSONL log."""
    _ensure_parent_dir()
    entry = {
        "ts": datetime.now().isoformat(),
        "query": query,
        "results": results,
    }
    # Atomic write: write to temp then rename
    tmp_path = FINDINGS_PATH.with_suffix(".tmp")
    try:
        # Read existing content
        existing = []
        if FINDINGS_PATH.exists():
            with open(FINDINGS_PATH, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            existing.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

        # Append new entry
        existing.append(entry)

        # Write all to temp
        with open(tmp_path, "w") as f:
            for e in existing:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

        # Atomic rename
        os.replace(tmp_path, FINDINGS_PATH)
    except Exception:
        # Best effort cleanup
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


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