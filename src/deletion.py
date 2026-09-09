"""Permanent session deletion with an explicit, reviewable scope."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Callable

from database import Database
from events import EventStore, legacy_session_id
from interior import InteriorStore


class DeletionError(ValueError):
    pass


@dataclass
class DeletionPlan:
    session_id: str
    title: str
    event_ids: set[str]
    paths: dict[str, set[str]]
    identity_ids: set[str]
    unresolved: list[str]
    boundary_times: set[str]

    @property
    def counts(self) -> dict[str, int]:
        return {name: len(ids) for name, ids in self.paths.items() if ids}

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "counts": self.counts,
            "event_ids": sorted(self.event_ids),
            "unresolved": self.unresolved,
            "backup_disclosure": (
                "This removes matching records from Sage's active local stores. "
                "External backups are outside Sage and are not erased by this action."
            ),
        }


def _read_jsonl(path: Path) -> list[object]:
    return EventStore._read_jsonl(path)


def _session_records(store: EventStore, session_id: str) -> tuple[set[str], set[int], set[int]]:
    event_ids: set[str] = set()
    event_indexes: set[int] = set()
    control_indexes: set[int] = set()
    active = legacy_session_id(-1)
    records = store._read_records()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        kind = record.get("kind")
        if kind == "chat_boundary":
            active = store._record_session_id(record, legacy_session_id(index))
            if active == session_id:
                control_indexes.add(index)
        elif kind in {"session_metadata", "session_open"} and record.get("session_id") == session_id:
            control_indexes.add(index)
        elif record.get("role") in {"user", "assistant"}:
            event_session = store._record_session_id(record, active)
            if event_session == session_id:
                event_indexes.add(index)
                event_ids.add(record.get("id", f"legacy:{index}"))
    return event_ids, event_indexes, control_indexes


def build_deletion_plan(store: EventStore, interior: InteriorStore, session_id: str) -> DeletionPlan:
    summary = store._session_summary(session_id)
    event_ids, event_indexes, control_indexes = _session_records(store, session_id)
    paths: dict[str, set[str]] = {
        "events": event_indexes | control_indexes,
        "transcript_corrections": set(),
        "entities": set(),
        "heartbeat": set(),
        "searches": set(),
        "embeddings": set(),
        "reflections": set(),
        "metabolism": set(),
        "waiting_message": set(),
        "identity": set(),
    }
    for name, path, source_key in (
        ("transcript_corrections", store.path, "source_event_id"),
        ("entities", store.entities_path, "source_event_id"),
        ("heartbeat", store.heartbeat_path, "source_event_id"),
        ("searches", store.searches_path, "source_event_id"),
        ("embeddings", store.embeddings_path, "event_id"),
        ("reflections", interior.reflections_path, "source_event_id"),
        ("metabolism", interior.metabolism_path, "source_event_id"),
    ):
        for index, record in enumerate(_read_jsonl(path)):
            if isinstance(record, dict) and record.get(source_key) in event_ids:
                paths[name].add(str(index))

    unresolved: list[str] = []
    reflection_ids = {
        str(record.get("id"))
        for index, record in enumerate(_read_jsonl(interior.reflections_path))
        if str(index) in paths["reflections"] and isinstance(record, dict) and record.get("id")
    }
    identity_records = _read_jsonl(interior.identity_path)
    removed_proposals: set[str] = set()
    for index, record in enumerate(identity_records):
        if not isinstance(record, dict):
            continue
        if record.get("kind") == "proposal":
            source_ids = set(record.get("source_event_ids") or [])
            evidence = set(record.get("evidence") or [])
            if source_ids and source_ids <= event_ids:
                paths["identity"].add(str(index))
                removed_proposals.add(str(record.get("id")))
            elif source_ids & event_ids or evidence & reflection_ids:
                unresolved.append(f"identity proposal {record.get('id')} has shared provenance")
        elif record.get("kind") == "ruling" and record.get("target_id") in removed_proposals:
            paths["identity"].add(str(index))
    waiting = interior.get_waiting_message()
    if waiting and waiting.get("source_event_id") in event_ids:
        paths["waiting_message"].add("0")
    boundary_times = {
        str(record.get("said_at"))
        for index, record in enumerate(store._read_records())
        if index in control_indexes and isinstance(record, dict) and record.get("kind") == "chat_boundary"
    }
    return DeletionPlan(session_id, summary["title"], event_ids, paths, removed_proposals, unresolved, boundary_times)


def _stage_jsonl(path: Path, remove_indexes: set[str], staged: list[Path]) -> None:
    if not path.exists() or not remove_indexes:
        return
    records = _read_jsonl(path)
    remove = {str(index) for index in remove_indexes}
    temp = path.with_name(f".{path.name}.delete-tmp")
    with temp.open("w", encoding="utf-8") as stream:
        for index, record in enumerate(records):
            if str(index) not in remove:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    staged.append(temp)


def execute_deletion(
    store: EventStore,
    interior: InteriorStore,
    relational: Database | None,
    interior_db: Database | None,
    plan: DeletionPlan,
    confirmation: str,
    *,
    before_commit: Callable[[], None] | None = None,
) -> dict[str, Any]:
    if confirmation != "DELETE":
        raise DeletionError("Type DELETE exactly to permanently delete this chat")
    if plan.unresolved:
        raise DeletionError("Deletion scope includes derived records without safe provenance")
    staged: list[Path] = []
    files = {
        "entities": store.entities_path,
        "heartbeat": store.heartbeat_path,
        "searches": store.searches_path,
        "embeddings": store.embeddings_path,
        "reflections": interior.reflections_path,
        "metabolism": interior.metabolism_path,
    }
    try:
        # events.jsonl has one shared file; merge its removal indexes.
        _stage_jsonl(store.path, plan.paths["events"] | plan.paths["transcript_corrections"], staged)
        for name, path in files.items():
            _stage_jsonl(path, plan.paths[name], staged)
        if plan.paths["waiting_message"] and interior.waiting_message_path.exists():
            staged.append(interior.waiting_message_path.with_name(f".{interior.waiting_message_path.name}.delete-tmp"))
            staged[-1].write_text("", encoding="utf-8")
            with staged[-1].open("a", encoding="utf-8") as stream:
                stream.flush()
                os.fsync(stream.fileno())
        if plan.paths["identity"]:
            _stage_jsonl(interior.identity_path, plan.paths["identity"], staged)
        if before_commit:
            before_commit()
        if relational:
            relational.delete_session(plan.session_id, plan.event_ids, plan.boundary_times)
        if interior_db:
            interior_db.delete_sources(plan.event_ids, plan.identity_ids, bool(plan.paths["waiting_message"]))
        for temp in staged:
            target = temp.with_name(temp.name[1:].replace(".delete-tmp", ""))
            os.replace(temp, target)
        store._current_session_id = store._active_session_id(store._read_records()) if store._read_records() else ""
        store._resumed_session_id = None
        return plan.as_dict()
    except Exception:
        for temp in staged:
            temp.unlink(missing_ok=True)
        raise
