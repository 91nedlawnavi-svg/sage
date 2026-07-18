import json
import os
from pathlib import Path
from datetime import datetime
from config.settings import REFLECTIONS_PATH
from utils.logger import warning


def _ensure_parent_dir():
    """Ensure the parent directory for reflections exists."""
    REFLECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)


def append_reflection(text: str, idle_seconds: float) -> None:
    """Append a reflection entry to the JSONL log."""
    _ensure_parent_dir()
    entry = {
        "ts": datetime.now().isoformat(),
        "idle_seconds": round(idle_seconds, 1),
        "text": text,
    }
    # True append (O_APPEND): one JSONL line per call. The old read-all/
    # rewrite-all pattern raced concurrent writers — blueprint Wave 1 #4.
    try:
        with open(REFLECTIONS_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        # never raise into the heartbeat path — but never fail silently either
        warning(f"reflection_log/append failed: {e}")


def read_recent(n: int = 20) -> list[dict]:
    """Read the most recent N reflections."""
    if not REFLECTIONS_PATH.exists():
        return []
    entries = []
    try:
        with open(REFLECTIONS_PATH, "r") as f:
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