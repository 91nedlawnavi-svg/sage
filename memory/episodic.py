"""
memory/episodic.py — Episodic memory layer

Stores distilled summaries of concrete events:
  - timestamped
  - named for retrieval
  - written by the Qwen memory model, not raw logs

Format per file:
  [date]
  [summary text]
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import EPISODIC_DIR
from memory.storage import (
    ensure_dirs,
    list_memory_files,
    read_memory_entry,
    ts_filename,
    write_memory_entry,
)


def _episode_stem(label: str = "") -> str:
    """Generate episodic filename: YYYYMMDD_HHMMSS_label."""
    date_part = datetime.now().strftime("%Y%m%d_%H%M%S")
    if label:
        safe = re.sub(r"[^a-z0-9_-]", "_", label.lower())[:32]
        return f"{date_part}_{safe}"
    return date_part


async def write_episode(summary: str, label: str = "") -> Path:
    """
    Persist one episodic memory entry.
    summary should be a distilled interpretation, not a raw log.
    """
    ensure_dirs(EPISODIC_DIR)
    stem = _episode_stem(label)
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M")
    content = f"[{ts}]\n{summary.strip()}\n"
    return await write_memory_entry(EPISODIC_DIR, stem, content)


async def load_recent_episodes(n: int = 10) -> list[str]:
    """Load the n most recent episodic memory entries."""
    files = await list_memory_files(EPISODIC_DIR)
    recent = files[-n:]  # files are sorted ascending by name (timestamp)
    entries = []
    for f in reversed(recent):  # newest first
        content = await read_memory_entry(f)
        if content:
            entries.append(content.strip())
    return entries


async def load_all_episodes() -> list[tuple[Path, str]]:
    """Load all episodes as (path, content) pairs. Used by retrieval."""
    files = await list_memory_files(EPISODIC_DIR)
    result = []
    for f in files:
        content = await read_memory_entry(f)
        if content:
            result.append((f, content))
    return result
