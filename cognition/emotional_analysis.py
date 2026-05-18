"""
cognition/emotional_analysis.py — Emotional pattern extraction

After each conversation segment, this module:
  1. Calls the memory model to extract emotional themes
  2. Merges new observations into existing theme files
  3. Creates new theme files for newly detected patterns

This runs asynchronously — never blocks the conversation.
"""

import json
from typing import Optional

import httpx

from memory.emotional import load_theme, write_emotional_theme
from models.inference import mem_complete
from models.prompts import (
    EMOTIONAL_EXTRACT_SYSTEM,
    EMOTIONAL_MERGE_SYSTEM,
    emotional_extract_prompt,
    emotional_merge_prompt,
)
from utils.logger import log


def _parse_themes(raw: str) -> list[dict]:
    """Parse JSON array of theme dicts from model output."""
    try:
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except Exception as e:
        log("cognition", "emotional_json_parse_failed", error=str(e))
    return []


async def extract_and_persist_emotions(
    conversation_digest: str,
    client: httpx.AsyncClient,
) -> list[str]:
    """
    Extract emotional themes from a conversation digest and persist them.
    Returns list of theme names that were updated.

    conversation_digest: last N turns formatted as "ROLE: content"
    """
    # 1. Extract new themes from conversation
    raw = await mem_complete(
        system=EMOTIONAL_EXTRACT_SYSTEM,
        user=emotional_extract_prompt(conversation_digest),
        client=client,
        max_tokens=600,
    )
    if not raw:
        return []

    themes = _parse_themes(raw)
    if not themes:
        log("cognition", "emotional_parse_failed")
        return []

    updated = []

    for item in themes:
        theme_name    = item.get("theme", "").strip()
        interpretation = item.get("interpretation", "").strip()

        if not theme_name or not interpretation:
            continue

        # 2. Check if theme already exists
        existing = await load_theme(theme_name)

        if existing:
            # Merge new observation into existing theme
            merged = await mem_complete(
                system=EMOTIONAL_MERGE_SYSTEM,
                user=emotional_merge_prompt(existing, interpretation),
                client=client,
                max_tokens=300,
            )
            final_text = merged if merged else interpretation
        else:
            final_text = interpretation

        # 3. Persist
        await write_emotional_theme(theme_name, final_text)
        log("cognition", "emotional_theme_updated", theme=theme_name, merged=bool(existing))
        updated.append(theme_name)

    return updated
