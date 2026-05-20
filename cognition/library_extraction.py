"""
cognition/library_extraction.py — Automatic library population

Daemon step 4. Reads a conversation digest, asks Qwen to identify
notable people, places, and topics, then writes or merges entries
into ~/sage_data/library/{category}/{name}.txt

Design:
  - Notes everything (Elliot cleans up manually)
  - Merges intelligently with existing entries (like emotional themes)
  - Prose format, third person
  - Never overwrites with less information than already exists
"""

import json
from typing import Optional

import httpx

from config import LIBRARY_CATS, LIBRARY_DIR
from memory.storage import ensure_dirs, read_memory_entry, safe_stem, write_text
from models.inference import mem_complete
from models.prompts import (
    LIBRARY_EXTRACT_SYSTEM,
    LIBRARY_MERGE_SYSTEM,
    library_extract_prompt,
    library_merge_prompt,
)
from utils.logger import log


def _entry_path(category: str, name: str):
    """Resolve the file path for a library entry."""
    return LIBRARY_DIR / category / f"{safe_stem(name)}.txt"


async def _load_entry(category: str, name: str) -> Optional[str]:
    """Read an existing library entry. Returns None if absent."""
    path = _entry_path(category, name)
    if not path.exists():
        return None
    return await read_memory_entry(path)


async def _write_entry(category: str, name: str, content: str) -> None:
    """Write a library entry file."""
    ensure_dirs(LIBRARY_DIR / category)
    path = _entry_path(category, name)
    await write_text(path, content.strip() + "\n")


def _parse_entities(raw: str) -> list[dict]:
    """Parse JSON array from model output. Returns [] on failure."""
    try:
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except Exception as e:
        log("cognition", "library_json_parse_failed", error=str(e))
    return []


async def extract_and_populate_library(
    conversation_digest: str,
    client: httpx.AsyncClient,
) -> list[str]:
    """
    Extract notable entities from a conversation digest and write
    or merge them into the library.

    Returns a list of written entry paths (for logging).
    """
    raw = await mem_complete(
        system=LIBRARY_EXTRACT_SYSTEM,
        user=library_extract_prompt(conversation_digest),
        client=client,
        max_tokens=600,
    )
    if not raw:
        return []

    entities = _parse_entities(raw)
    if not entities:
        return []

    written = []

    for item in entities:
        category = item.get("category", "").strip()
        name     = item.get("name", "").strip()
        note     = item.get("note", "").strip()

        if not category or not name or not note:
            continue

        # Only write to allowed categories
        if category not in LIBRARY_CATS:
            continue

        existing = await _load_entry(category, name)

        if existing:
            # Merge new observation into existing entry
            merged = await mem_complete(
                system=LIBRARY_MERGE_SYSTEM,
                user=library_merge_prompt(existing, note),
                client=client,
                max_tokens=300,
            )
            final = merged if merged else existing + "\n\n" + note
        else:
            final = note

        await _write_entry(category, name, final)
        log("cognition", "library_entry_written",
            category=category, name=name, merged=bool(existing))
        written.append(f"{category}/{safe_stem(name)}")

    return written
