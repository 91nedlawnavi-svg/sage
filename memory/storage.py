"""
memory/storage.py — Filesystem I/O primitives

All disk operations go through here.
Single async lock prevents concurrent write corruption.
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

_file_lock = asyncio.Lock()


# ── Low-level I/O ────────────────────────────────────────────────────

async def read_text(path: Path) -> str:
    """Read a text file. Returns '' on missing or error."""
    async with _file_lock:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""


async def write_text(path: Path, content: str) -> None:
    """Overwrite a file atomically (write to .tmp, then rename)."""
    async with _file_lock:
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(path)
        except Exception:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise


async def append_text(path: Path, line: str) -> None:
    """Append a line to a file."""
    async with _file_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


def ensure_dirs(*dirs: Path) -> None:
    """Create directories if missing."""
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def safe_stem(name: str) -> str:
    """Convert a name to a safe filesystem stem."""
    return "".join(
        c if (c.isalnum() or c in "-_") else "_"
        for c in name.lower()
    ).strip("_")


# ── Chat history ─────────────────────────────────────────────────────

async def load_history(history_file: Path) -> list[dict]:
    """Load JSONL chat history. Each line is {role, content, ts}."""
    raw = await read_text(history_file)
    messages = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except Exception:
            pass
    return messages


async def append_history(history_file: Path, role: str, content: str) -> None:
    """Append one turn to the JSONL history file."""
    entry = json.dumps({"role": role, "content": content, "ts": time.time()})
    await append_text(history_file, entry + "\n")


def history_for_prompt(history: list[dict], n: int) -> list[dict]:
    """Slice the last n*2 messages for prompt injection."""
    recent = history[-(n * 2):]
    return [{"role": m["role"], "content": m["content"]} for m in recent]


# ── Timestamped memory files ─────────────────────────────────────────

def ts_filename(prefix: str = "") -> str:
    """Generate a timestamp-based filename stem."""
    return f"{prefix}{datetime.now().strftime('%Y%m%d_%H%M%S')}"


async def write_memory_entry(directory: Path, stem: str, content: str) -> Path:
    """Write a single memory entry file."""
    path = directory / f"{stem}.txt"
    await write_text(path, content)
    return path


async def list_memory_files(directory: Path) -> list[Path]:
    """List all .txt files in a memory directory, sorted by name."""
    if not directory.exists():
        return []
    return sorted(directory.glob("*.txt"))


async def read_memory_entry(path: Path) -> Optional[str]:
    """Read one memory file. Returns None if missing."""
    content = await read_text(path)
    return content if content.strip() else None
