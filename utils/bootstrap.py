"""
utils/bootstrap.py — First-run setup and legacy data migration

Handles:
  1. Directory creation
  2. Migration of legacy library/ files (people, places, etc.) → library/
     (they're already in the right format; we just ensure they're in place)
  3. Distillation of legacy chat_history.txt into episodic + emotional memory
     (runs once on first boot if chat_history exists and episodic/ is empty)
"""

import json
import shutil
from pathlib import Path
from typing import Optional

import httpx

from config import (
    BASE_DIR,
    DATA_DIR,
    DIRECTIVE_FILE,
    EMOTIONAL_DIR,
    EMBEDDINGS_DIR,
    EPISODIC_DIR,
    HISTORY_FILE,
    LIBRARY_CATS,
    LIBRARY_DIR,
    REFLECTIONS_DIR,
)
from memory.episodic import write_episode
from memory.emotional import write_emotional_theme
from memory.storage import ensure_dirs, load_history
from models.inference import mem_complete
from models.prompts import (
    BOOTSTRAP_EMOTIONAL_SYSTEM,
    BOOTSTRAP_EPISODIC_SYSTEM,
)
from utils.logger import log


DEFAULT_DIRECTIVE = """\
CRITICAL RULES — NO EXCEPTIONS:
- NEVER ask a question. Any response ending with "?" is a failed response.
- NEVER use banned phrases (list below)
- NEVER open with "I" or with the user's name
- NO bullet points or numbered lists
- Keep responses SHORT — 2-4 sentences unless topic demands more

You are Sage. You run locally on Elliot's machine. You are with him — not helping him.

Elliot is Indonesian. When he writes in Indonesian, respond in Indonesian. Do not switch languages unless he does.

When Elliot shifts topic, follow him immediately. Do not linger or circle back.

Say what needs saying once. Trust him to have heard it. Never lecture. Never moralize.

When he's venting, respond only if you have something real to add. Sit with him before offering anything.

BANNED PHRASES — NEVER USE:
"It sounds like" / "It seems like" / "I can see" / "That sounds"
"I'm here for you" / "You're not alone" / "That must be"
"It's okay" / "It's tough" / "It's natural" / "It's normal"
"""


def ensure_filesystem() -> None:
    """Create all required directories and default files."""
    ensure_dirs(
        BASE_DIR,
        DATA_DIR,
        EPISODIC_DIR,
        EMOTIONAL_DIR,
        REFLECTIONS_DIR,
        EMBEDDINGS_DIR,
        LIBRARY_DIR,
    )
    for cat in LIBRARY_CATS:
        ensure_dirs(LIBRARY_DIR / cat)

    if not DIRECTIVE_FILE.exists():
        DIRECTIVE_FILE.write_text(DEFAULT_DIRECTIVE, encoding="utf-8")
        log("bootstrap", "directive_created")

    if not HISTORY_FILE.exists():
        HISTORY_FILE.write_text("", encoding="utf-8")

    log("bootstrap", "filesystem_ready", path=str(BASE_DIR))


def _parse_json_list(raw: str) -> list[dict]:
    try:
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def distill_legacy_history(client: httpx.AsyncClient) -> None:
    """
    Run once on first boot.
    Reads legacy chat_history.txt and distills into episodic + emotional memory.
    Skips if episodic/ already has files (already done).
    """
    existing_episodes = list(EPISODIC_DIR.glob("*.txt"))
    if existing_episodes:
        log("bootstrap", "distillation_skipped", reason="episodic_exists")
        return

    if not HISTORY_FILE.exists():
        return

    history = await load_history(HISTORY_FILE)
    if not history:
        log("bootstrap", "distillation_skipped", reason="no_history")
        return

    log("bootstrap", "distillation_start", turns=len(history))

    # Format full history as a readable digest
    digest = "\n".join(
        f"{m['role'].upper()}: {m['content']}"
        for m in history
    )

    # Truncate if huge (Qwen 3B has 2048 ctx)
    digest = digest[-6000:]

    # Extract episodic events
    raw_episodic = await mem_complete(
        system=BOOTSTRAP_EPISODIC_SYSTEM,
        user=f"CHAT HISTORY:\n{digest}\n\nExtract episodic memories:",
        client=client,
        max_tokens=800,
    )
    if raw_episodic:
        items = _parse_json_list(raw_episodic)
        for item in items:
            label   = item.get("label", "event").strip()
            summary = item.get("summary", "").strip()
            if summary:
                await write_episode(summary, label)
                log("bootstrap", "episodic_written", label=label)

    # Extract emotional themes
    raw_emotional = await mem_complete(
        system=BOOTSTRAP_EMOTIONAL_SYSTEM,
        user=f"CHAT HISTORY:\n{digest}\n\nExtract emotional themes:",
        client=client,
        max_tokens=800,
    )
    if raw_emotional:
        items = _parse_json_list(raw_emotional)
        for item in items:
            theme  = item.get("theme", "").strip()
            interp = item.get("interpretation", "").strip()
            if theme and interp:
                await write_emotional_theme(theme, interp)
                log("bootstrap", "emotional_written", theme=theme)

    log("bootstrap", "distillation_complete")
