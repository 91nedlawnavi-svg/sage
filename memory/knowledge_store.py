"""
Knowledge store — Phase 4 Layer 2.

Append-only JSONL notebooks for derived relational and interior knowledge.
Parametrized by notebook name ("relational" or "interior") so the same code
drives both notebooks.
"""

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from config.settings import (
    INTERIOR_ENTITIES_PATH,
    INTERIOR_RELATIONS_PATH,
    KNOWLEDGE_DIR,
    RELATIONAL_ENTITIES_PATH,
    RELATIONAL_RELATIONS_PATH,
)


# ── Notebook resolver ──────────────────────────────────────────────

_NOTEBOOK_PATHS: dict[str, tuple[Path, Path]] = {
    "relational": (RELATIONAL_ENTITIES_PATH, RELATIONAL_RELATIONS_PATH),
    "interior": (INTERIOR_ENTITIES_PATH, INTERIOR_RELATIONS_PATH),
}

_VALID_ORIGINS = frozenset(["she", "elliot"])


def _resolve_notebook(notebook: str) -> tuple[Path, Path]:
    """Map a notebook name to (entity_path, relation_path)."""
    paths = _NOTEBOOK_PATHS.get(notebook)
    if paths is None:
        raise ValueError(f"Unknown notebook: {notebook!r}. Expected 'relational' or 'interior'.")
    return paths


# ── ID helpers (deterministic) ─────────────────────────────────────

def _slug(text: str) -> str:
    """Lowercase, collapse runs of non-alphanumeric to single hyphens, trim."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def make_entity_id(type_: str, name: str) -> str:
    """Deterministic entity ID: ``{type}:{slug(name)}``."""
    return f"{type_}:{_slug(name)}"


def make_relation_id(subject_id: str, predicate: str, object_value: str) -> str:
    """First 16 hex chars of SHA1 over ``{subject_id}|{predicate}|{object_value}``."""
    raw = f"{subject_id}|{predicate}|{object_value}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ── Directory helper ───────────────────────────────────────────────

def _ensure_dir() -> None:
    """Create KNOWLEDGE_DIR (and parents) if missing."""
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)


# ── Internal append (atomic tmp → rename, exactly like reflection_log.py) ──

def _append_line(path: Path, entry: dict) -> None:
    """Read existing, append entry, write tmp, rename — never raises."""
    _ensure_dir()
    tmp_path = path.with_suffix(".tmp")
    try:
        existing = []
        if path.exists():
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            existing.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

        existing.append(entry)

        with open(tmp_path, "w", encoding="utf-8") as f:
            for e in existing:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


# ── Public append functions ────────────────────────────────────────

def append_entity(
    notebook: str,
    *,
    id: str,
    type: str,
    name: str,
    aliases: list[str] | None = None,
    origin: str,
    locked: bool = False,
) -> None:
    """Append one entity record. Degrades to no-op on any failure."""
    if origin not in _VALID_ORIGINS:
        return
    try:
        entity_path, _ = _resolve_notebook(notebook)
    except ValueError:
        return
    now = datetime.now().isoformat()
    entry = {
        "id": id,
        "kind": "entity",
        "type": type,
        "name": name,
        "aliases": aliases or [],
        "origin": origin,
        "locked": locked,
        "first_seen": now,
        "last_seen": now,
        "ts": now,
    }
    _append_line(entity_path, entry)


def append_relation(
    notebook: str,
    *,
    id: str,
    subject_id: str,
    predicate: str,
    object_value: str,
    object_kind: str,
    provenance: list[str] | None = None,
    confidence: float = 0.0,
    origin: str,
    locked: bool = False,
) -> None:
    """Append one relation record. Degrades to no-op on any failure."""
    if origin not in _VALID_ORIGINS:
        return
    if object_kind not in ("entity", "literal"):
        return
    try:
        _, relation_path = _resolve_notebook(notebook)
    except ValueError:
        return
    now = datetime.now().isoformat()
    entry = {
        "id": id,
        "kind": "relation",
        "subject_id": subject_id,
        "predicate": predicate,
        "object": {"kind": object_kind, "value": object_value},
        "provenance": provenance or [],
        "confidence": confidence,
        "origin": origin,
        "locked": locked,
        "first_seen": now,
        "last_seen": now,
        "ts": now,
    }
    _append_line(relation_path, entry)


# ── Load functions (safe, return [] on failure) ────────────────────

def _read_jsonl_safe(path: Path) -> list[dict]:
    """Read a JSONL file; return [] on any failure."""
    if not path.exists():
        return []
    entries: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
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


def load_entities(notebook: str) -> list[dict]:
    """Load all entity records from *notebook*."""
    try:
        entity_path, _ = _resolve_notebook(notebook)
    except ValueError:
        return []
    return _read_jsonl_safe(entity_path)


def load_relations(notebook: str) -> list[dict]:
    """Load all relation records from *notebook*."""
    try:
        _, relation_path = _resolve_notebook(notebook)
    except ValueError:
        return []
    return _read_jsonl_safe(relation_path)


# ── Self-test ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import shutil
    import tempfile
    import config.settings as _settings

    # Point the "interior" notebook at a temp directory for isolation
    tmp = Path(tempfile.mkdtemp())
    _NOTEBOOK_PATHS["interior"] = (
        tmp / "interior_entities.jsonl",
        tmp / "interior_relations.jsonl",
    )

    # Append 2 entities
    e1_id = make_entity_id("person", "Ada Lovelace")
    e2_id = make_entity_id("concept", "Analytical Engine")

    append_entity(
        "interior",
        id=e1_id,
        type="person",
        name="Ada Lovelace",
        aliases=["Ada Byron"],
        origin="she",
    )
    append_entity(
        "interior",
        id=e2_id,
        type="concept",
        name="Analytical Engine",
        origin="elliot",
    )

    # Append 1 relation linking them
    r_id = make_relation_id(e1_id, "designed", e2_id)
    append_relation(
        "interior",
        id=r_id,
        subject_id=e1_id,
        predicate="designed",
        object_value=e2_id,
        object_kind="entity",
        provenance=["reflection_2024"],
        confidence=0.95,
        origin="she",
    )

    # Load back
    entities = load_entities("interior")
    relations = load_relations("interior")

    # Assert round-trip
    assert len(entities) == 2, f"Expected 2 entities, got {len(entities)}"
    assert len(relations) == 1, f"Expected 1 relation, got {len(relations)}"

    assert entities[0]["id"] == e1_id
    assert entities[0]["kind"] == "entity"
    assert entities[0]["type"] == "person"
    assert entities[0]["name"] == "Ada Lovelace"
    assert entities[0]["aliases"] == ["Ada Byron"]
    assert entities[0]["origin"] == "she"
    assert entities[0]["locked"] is False
    assert "first_seen" in entities[0]
    assert "last_seen" in entities[0]
    assert "ts" in entities[0]

    assert entities[1]["id"] == e2_id
    assert entities[1]["kind"] == "entity"
    assert entities[1]["type"] == "concept"
    assert entities[1]["name"] == "Analytical Engine"
    assert entities[1]["aliases"] == []
    assert entities[1]["origin"] == "elliot"
    assert entities[1]["locked"] is False

    assert relations[0]["id"] == r_id
    assert relations[0]["kind"] == "relation"
    assert relations[0]["subject_id"] == e1_id
    assert relations[0]["predicate"] == "designed"
    assert relations[0]["object"]["kind"] == "entity"
    assert relations[0]["object"]["value"] == e2_id
    assert relations[0]["provenance"] == ["reflection_2024"]
    assert relations[0]["confidence"] == 0.95
    assert relations[0]["origin"] == "she"
    assert relations[0]["locked"] is False

    # Assert no .tmp file left behind
    tmp_files = list(tmp.glob("*.tmp"))
    assert len(tmp_files) == 0, f"Leftover .tmp files: {tmp_files}"

    # Clean up
    shutil.rmtree(tmp)

    print("OK")
