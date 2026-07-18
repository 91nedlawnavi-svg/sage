"""Conversation log — append-only JSONL for chat history (Phase 4 Layer 0)."""
import json
import os
from pathlib import Path
from datetime import datetime
from config.settings import CONVERSATION_PATH
from utils.logger import warning


def _ensure_parent_dir():
    """Ensure the parent directory for conversation log exists."""
    CONVERSATION_PATH.parent.mkdir(parents=True, exist_ok=True)


def append_message(role: str, content: str) -> None:
    """Append a chat message to the conversation log.

    role: "user" or "assistant"
    """
    if role not in ("user", "assistant"):
        return
    if not content or not content.strip():
        return

    _ensure_parent_dir()
    entry = {
        "id": f"{role}_{int(datetime.now().timestamp() * 1000)}",
        "role": role,
        "content": content,
        "ts": datetime.now().isoformat(),
    }

    # True append (O_APPEND): one JSONL line per call. The old read-all/
    # rewrite-all "atomic" pattern raced concurrent writers (last rewrite
    # wins, dropping the other's entry) — blueprint Wave 1 #4.
    try:
        with open(CONVERSATION_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        # never raise into the chat path — but never fail silently either
        warning(f"conversation_log/append failed: {e}")


def load_all() -> list[dict]:
    """Load all conversation entries in order (oldest first)."""
    if not CONVERSATION_PATH.exists():
        return []
    entries = []
    try:
        with open(CONVERSATION_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        return []
    return entries


def rotate_log() -> str | None:
    """Archive current conversation log with timestamp.

    Returns the archive path or None if no log exists / failed.
    """
    if not CONVERSATION_PATH.exists():
        return None
    archive_name = f"conversation.{datetime.now().isoformat().replace(':', '-')}.jsonl"
    archive_path = CONVERSATION_PATH.parent / archive_name
    try:
        os.rename(CONVERSATION_PATH, archive_path)
        return str(archive_path)
    except Exception:
        return None