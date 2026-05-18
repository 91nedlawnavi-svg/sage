"""
utils/logger.py — Sage structured runtime logger

JSONL log file: ~/sage_data/logs/sage.2026-05-14.jsonl  (daily rotation)
Each line is a self-contained JSON event.

Usage:
    from utils.logger import log

    log("daemon", "cycle_start")
    log("retrieval", "scored", candidates=12, above_threshold=3, top_score=0.72)
    log("daemon", "error", step="episodic", error=str(e))

Fields always present:
    ts        ISO-8601 timestamp
    subsystem one of: daemon, retrieval, cognition, memory, inference, bootstrap
    event     short snake_case label
    ...rest   optional keyword args
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Resolved lazily so config import doesn't cause circular issues at module load
_log_dir: Path | None = None


def _get_log_path() -> Path:
    global _log_dir
    if _log_dir is None:
        from config import BASE_DIR
        _log_dir = BASE_DIR / "logs"
        _log_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _log_dir / f"sage.{date_str}.jsonl"


def log(subsystem: str, event: str, **kwargs) -> None:
    """
    Write one structured log event.
    Never raises — logging failures are printed to stderr and swallowed.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "subsystem": subsystem,
        "event": event,
        **kwargs,
    }
    try:
        with _get_log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[logger] Failed to write log: {exc}", file=sys.stderr)
