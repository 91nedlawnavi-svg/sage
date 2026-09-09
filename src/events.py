"""Append-only timestamped event persistence with vector embedding support for Sage."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict
from uuid import NAMESPACE_URL, uuid4, uuid5

from router import EmbeddingClient

if TYPE_CHECKING:
    from database import Database

_log = logging.getLogger(__name__)

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "how",
    "in",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
}


class Event(TypedDict):
    role: Literal["user", "assistant"]
    content: str
    said_at: str
    session_id: str
    id: NotRequired[str]
    source: NotRequired[Literal["text", "voice"]]
    call_id: NotRequired[str]
    turn_id: NotRequired[str]
    original_content: NotRequired[str]


class TranscriptCorrection(TypedDict):
    kind: Literal["transcript_correction"]
    id: str
    source_event_id: str
    content: str
    said_at: str


class EntityObservation(TypedDict):
    kind: Literal["entity_obs"]
    entity_id: str
    name: str
    observation: str
    said_at: str
    source_event_id: NotRequired[str]


class HeartbeatCompletion(TypedDict):
    kind: Literal["heartbeat"]
    stage: Literal["entities", "reflection", "metabolism"]
    source_event_id: str
    said_at: str


class SearchSource(TypedDict):
    title: str
    snippet: str
    url: str


class SearchRecord(TypedDict):
    kind: Literal["search"]
    id: str
    query: str
    sources: list[SearchSource]
    origin: Literal["conversation", "metabolism"]
    source_event_id: str
    said_at: str


class ChatBoundary(TypedDict):
    kind: Literal["chat_boundary"]
    said_at: str
    session_id: NotRequired[str]


class SessionMetadata(TypedDict):
    kind: Literal["session_metadata"]
    id: str
    session_id: str
    said_at: str
    title: NotRequired[str]
    archived: NotRequired[bool]


class SessionOpen(TypedDict):
    kind: Literal["session_open"]
    id: str
    session_id: str
    said_at: str


class SessionSummary(TypedDict):
    id: str
    title: str
    created_at: str
    last_active_at: str
    event_count: int
    archived: bool
    active: bool


def legacy_session_id(boundary_index: int) -> str:
    """Return the stable read-time ID for a session not tagged in old JSONL."""
    return str(uuid5(NAMESPACE_URL, f"sage:legacy-session:{boundary_index}"))


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class EventStore:
    def __init__(
        self,
        data_root: Path | None = None,
        embedder: EmbeddingClient | None = None,
        *,
        mirror: Database | None = None,
    ) -> None:
        self.data_root = data_root or Path.home() / "sage_data"
        self.relational_dir = self.data_root / "relational"
        self.interior_dir = self.data_root / "interior"
        self.path = self.data_root / "events.jsonl"
        self.embeddings_path = self.relational_dir / "embeddings.jsonl"
        self.entities_path = self.relational_dir / "entities.jsonl"
        self.heartbeat_path = self.relational_dir / "heartbeat.jsonl"
        self.searches_path = self.relational_dir / "searches.jsonl"
        self.embedder = embedder
        self._mirror = mirror
        self._write_lock = threading.RLock()
        records = self._read_records()
        self._current_session_id = self._active_session_id(records) if records else str(uuid4())
        self._resumed_session_id = self._resumed_session(records, self._current_session_id)

    def append(
        self,
        role: Literal["user", "assistant"],
        content: str,
        *,
        save_embedding: bool = True,
        source: Literal["text", "voice"] = "text",
        call_id: str | None = None,
        turn_id: str | None = None,
        session_id: str | None = None,
    ) -> Event:
        if (call_id is None) != (turn_id is None) or (call_id is not None and source != "voice"):
            raise ValueError("Call context requires a voice event with both call and turn IDs")
        if session_id is not None and not session_id:
            raise ValueError("Session ID must not be blank")
        with self._write_lock:
            event: Event = {
                "id": str(uuid4()),
                "role": role,
                "content": content,
                "said_at": self._timestamp(),
                "session_id": session_id or self._current_session_id,
                "source": source,
            }
            if call_id is not None and turn_id is not None:
                event["call_id"] = call_id
                event["turn_id"] = turn_id
            self._append_record(event)
            if session_id is None and role == "user":
                self._resumed_session_id = None
        self._mirror_event(event)
        if save_embedding and self.embedder is not None:
            try:
                self._save_embedding(event["id"], content)
            except OSError:
                pass
        return event

    def append_transcript_correction(self, source_event_id: str, content: str) -> TranscriptCorrection:
        source = next((event for event in self.history() if event["id"] == source_event_id), None)
        if source is None or source.get("source") != "voice":
            raise ValueError("Transcript corrections require a voice event")
        if not content.strip():
            raise ValueError("Transcript correction must not be blank")
        record: TranscriptCorrection = {
            "kind": "transcript_correction",
            "id": str(uuid4()),
            "source_event_id": source_event_id,
            "content": content,
            "said_at": self._timestamp(),
        }
        self._append_record(record)
        self._mirror_transcript_correction(record)
        if self.embedder is not None:
            try:
                self._save_embedding(source_event_id, content)
            except OSError:
                pass
        return record

    def transcript_corrections(self) -> list[TranscriptCorrection]:
        return [
            self._parse_transcript_correction(record)
            for record in self._read_records()
            if isinstance(record, dict) and record.get("kind") == "transcript_correction"
        ]

    def append_chat_boundary(self) -> ChatBoundary:
        record: ChatBoundary = {
            "kind": "chat_boundary",
            "said_at": self._timestamp(),
            "session_id": str(uuid4()),
        }
        with self._write_lock:
            self._append_record(record)
            self._current_session_id = record["session_id"]
            self._resumed_session_id = None
        self._mirror_chat_boundary(record)
        return record

    @property
    def current_session_id(self) -> str:
        return self._current_session_id

    def resumed_session_history(self) -> list[Event] | None:
        if self._resumed_session_id != self._current_session_id:
            return None
        return self.visible_history()

    def sessions(self, *, include_archived: bool = False) -> list[SessionSummary]:
        summaries: dict[str, SessionSummary] = {}
        automatic_titles: dict[str, str] = {}
        session_id = legacy_session_id(-1)
        for index, record in enumerate(self._read_records()):
            if not isinstance(record, dict):
                continue
            kind = record.get("kind")
            if kind == "chat_boundary":
                session_id = self._record_session_id(record, legacy_session_id(index))
                self._ensure_session_summary(summaries, session_id, record.get("said_at"))
            elif kind == "session_metadata":
                target = self._record_session_id(record, "")
                summary = summaries.get(target)
                if summary is None:
                    continue
                if "title" in record:
                    if not isinstance(record["title"], str) or not record["title"]:
                        raise ValueError("Invalid session title")
                    summary["title"] = record["title"]
                if "archived" in record:
                    if not isinstance(record["archived"], bool):
                        raise ValueError("Invalid session archive state")
                    summary["archived"] = record["archived"]
            elif record.get("role") in {"user", "assistant"}:
                event_session_id = self._record_session_id(record, session_id)
                summary = self._ensure_session_summary(summaries, event_session_id, record.get("said_at"))
                summary["event_count"] += 1
                said_at = record.get("said_at")
                if isinstance(said_at, str) and said_at > summary["last_active_at"]:
                    summary["last_active_at"] = said_at
                if record.get("role") == "user" and event_session_id not in automatic_titles:
                    automatic_titles[event_session_id] = self._automatic_session_title(record.get("content"))

        for summary in summaries.values():
            if not summary["title"]:
                summary["title"] = automatic_titles.get(summary["id"], "New chat")
            summary["active"] = summary["id"] == self._current_session_id
        result = [summary for summary in summaries.values() if include_archived or not summary["archived"]]
        result.sort(key=lambda summary: summary["last_active_at"], reverse=True)
        result.sort(key=lambda summary: not summary["active"])
        return result

    def open_session(self, session_id: str) -> SessionSummary:
        with self._write_lock:
            summary = self._session_summary(session_id)
            if summary["archived"]:
                raise ValueError("Archived chats must be restored before opening")
            if session_id != self._current_session_id:
                record: SessionOpen = {
                    "kind": "session_open",
                    "id": str(uuid4()),
                    "session_id": session_id,
                    "said_at": self._timestamp(),
                }
                self._append_record(record)
                self._current_session_id = session_id
                history = self.history()
                self._resumed_session_id = session_id if history and history[-1]["session_id"] != session_id else None
            return self._session_summary(session_id)

    def rename_session(self, session_id: str, title: str) -> SessionSummary:
        clean_title = " ".join(title.split())
        if not clean_title or len(clean_title) > 120:
            raise ValueError("Chat title must be 1 to 120 characters")
        with self._write_lock:
            self._session_summary(session_id)
            record: SessionMetadata = {
                "kind": "session_metadata",
                "id": str(uuid4()),
                "session_id": session_id,
                "said_at": self._timestamp(),
                "title": clean_title,
            }
            self._append_record(record)
        self._mirror_session_metadata(record)
        return self._session_summary(session_id)

    def archive_session(self, session_id: str) -> SessionSummary:
        return self._set_session_archived(session_id, True)

    def unarchive_session(self, session_id: str) -> SessionSummary:
        return self._set_session_archived(session_id, False)

    def _set_session_archived(self, session_id: str, archived: bool) -> SessionSummary:
        with self._write_lock:
            summary = self._session_summary(session_id)
            if summary["archived"] == archived:
                return summary
            record: SessionMetadata = {
                "kind": "session_metadata",
                "id": str(uuid4()),
                "session_id": session_id,
                "said_at": self._timestamp(),
                "archived": archived,
            }
            boundary: ChatBoundary | None = None
            if archived and session_id == self._current_session_id:
                boundary = {
                    "kind": "chat_boundary",
                    "said_at": self._timestamp(),
                    "session_id": str(uuid4()),
                }
                self._append_records([record, boundary])
                self._current_session_id = boundary["session_id"]
                self._resumed_session_id = None
            else:
                self._append_record(record)
        self._mirror_session_metadata(record)
        if boundary is not None:
            self._mirror_chat_boundary(boundary)
        return self._session_summary(session_id)

    def _session_summary(self, session_id: str) -> SessionSummary:
        if not isinstance(session_id, str) or not session_id:
            raise KeyError("Chat not found")
        summary = next((item for item in self.sessions(include_archived=True) if item["id"] == session_id), None)
        if summary is None:
            raise KeyError("Chat not found")
        return summary

    @staticmethod
    def _ensure_session_summary(
        summaries: dict[str, SessionSummary],
        session_id: str,
        said_at: object,
    ) -> SessionSummary:
        if not isinstance(said_at, str):
            raise ValueError("Invalid session timestamp")
        if session_id not in summaries:
            summaries[session_id] = {
                "id": session_id,
                "title": "",
                "created_at": said_at,
                "last_active_at": said_at,
                "event_count": 0,
                "archived": False,
                "active": False,
            }
        return summaries[session_id]

    @staticmethod
    def _automatic_session_title(content: object) -> str:
        if not isinstance(content, str):
            return "New chat"
        title = " ".join(content.split())
        if not title:
            return "New chat"
        return title if len(title) <= 48 else title[:47].rstrip() + "…"

    def append_entity_observation(
        self,
        entity_id: str,
        name: str,
        observation: str,
        *,
        source_event_id: str | None = None,
    ) -> EntityObservation:
        self.relational_dir.mkdir(parents=True, exist_ok=True)
        if source_event_id is not None:
            for existing in self.entity_observations():
                if existing.get("source_event_id") == source_event_id and existing["entity_id"] == entity_id:
                    return existing
        record: EntityObservation = {
            "kind": "entity_obs",
            "entity_id": entity_id,
            "name": name,
            "observation": observation,
            "said_at": self._timestamp(),
        }
        if source_event_id is not None:
            record["source_event_id"] = source_event_id
        with self.entities_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._mirror_entity_observation(record)
        return record

    def entity_observations(self) -> list[EntityObservation]:
        return [
            record for record in self._read_jsonl(self.entities_path)
            if isinstance(record, dict) and record.get("kind") == "entity_obs"
        ]

    def append_heartbeat_completion(
        self,
        stage: Literal["entities", "reflection", "metabolism"],
        source_event_id: str,
    ) -> HeartbeatCompletion:
        record: HeartbeatCompletion = {
            "kind": "heartbeat",
            "stage": stage,
            "source_event_id": source_event_id,
            "said_at": self._timestamp(),
        }
        self.relational_dir.mkdir(parents=True, exist_ok=True)
        with self.heartbeat_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._mirror_heartbeat_completion(record)
        return record

    def heartbeat_completed(self, stage: Literal["entities", "reflection", "metabolism"]) -> set[str]:
        completed: set[str] = set()
        for record in self._read_jsonl(self.heartbeat_path):
            if (
                isinstance(record, dict)
                and record.get("kind") == "heartbeat"
                and record.get("stage") == stage
                and isinstance(record.get("source_event_id"), str)
            ):
                completed.add(record["source_event_id"])
        return completed

    def append_search_record(
        self,
        query: str,
        sources: list[SearchSource],
        origin: Literal["conversation", "metabolism"],
        source_event_id: str,
    ) -> SearchRecord:
        record: SearchRecord = {
            "kind": "search",
            "id": str(uuid4()),
            "query": query,
            "sources": sources,
            "origin": origin,
            "source_event_id": source_event_id,
            "said_at": self._timestamp(),
        }
        self.relational_dir.mkdir(parents=True, exist_ok=True)
        with self.searches_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._mirror_search_record(record)
        return record

    def search_records(self) -> list[SearchRecord]:
        return [
            record for record in self._read_jsonl(self.searches_path)
            if isinstance(record, dict) and record.get("kind") == "search"
        ]

    def history(self) -> list[Event]:
        records = self._read_records()
        corrections: dict[str, str] = {}
        for record in records:
            if isinstance(record, dict) and record.get("kind") == "transcript_correction":
                correction = self._parse_transcript_correction(record)
                corrections[correction["source_event_id"]] = correction["content"]
        events: list[Event] = []
        session_id = legacy_session_id(-1)
        for index, record in enumerate(records):
            if isinstance(record, dict) and record.get("kind") == "chat_boundary":
                session_id = self._record_session_id(record, legacy_session_id(index))
                continue
            if isinstance(record, dict) and record.get("kind") in {
                "privacy",
                "session_metadata",
                "session_open",
                "transcript_correction",
            }:
                continue
            if self._is_legacy_search_event(record):
                continue
            event_session_id = self._record_session_id(record, session_id)
            event = self._parse_event(record, index, event_session_id)
            corrected = corrections.get(event["id"]) if event.get("source") == "voice" else None
            if corrected is not None:
                event["original_content"] = event["content"]
                event["content"] = corrected
            events.append(event)
        return events

    def read_all(self) -> list[Event]:
        return self.history()

    def visible_history(self) -> list[Event]:
        return [event for event in self.history() if event["session_id"] == self._current_session_id]

    def recall(
        self,
        query: str,
        limit: int = 8,
        *,
        exclude_event_id: str | None = None,
        fallback: bool = True,
    ) -> list[Event]:
        if limit <= 0:
            return []

        events = [
            event for event in self.history()
            if event["role"] in {"user", "assistant"}
        ]
        if exclude_event_id is not None:
            events = [event for event in events if event["id"] != exclude_event_id]

        if not events:
            return []

        if not query.strip():
            return events[-limit:] if fallback else []

        raw_terms = self._tokenize(query, keep_stop_words=True)
        query_terms = self._tokenize(query, keep_stop_words=len(raw_terms) <= 1)
        normalized_query = query.strip().lower()

        if not query_terms:
            return events[-limit:] if fallback else []

        # Check for vector embedding similarity if embedder available
        query_embedding: list[float] | None = None
        embeddings_map: dict[str, list[float]] = {}
        if self.embedder is not None:
            try:
                query_embedding = self.embedder.embed(query)
            except Exception:
                query_embedding = None
            if query_embedding:
                embeddings_map = self._load_embeddings()

        # Hybrid scoring: vector similarity + lexical overlap/frequency + exact match bonus
        scored: list[tuple[float, int, int, Event]] = []
        for index, event in enumerate(events):
            event_id = event["id"]
            event_content = event["content"].lower()
            event_tokens = self._tokenize(event_content, keep_stop_words=False)
            matched_terms = query_terms & event_tokens

            # Vector similarity component
            cos_sim = 0.0
            if query_embedding and event_id in embeddings_map:
                cos_sim = _cosine_similarity(query_embedding, embeddings_map[event_id])

            if not matched_terms and normalized_query not in event_content and cos_sim < 0.35:
                continue

            exact_match = 1 if normalized_query and normalized_query in event_content else 0
            overlap_score = len(matched_terms) / len(query_terms) if query_terms else 0.0
            term_freq_score = sum(event_content.count(term) for term in matched_terms) / max(len(event_tokens), 1)

            # Combined weighted score
            total_score = (
                overlap_score * 2.0
                + term_freq_score
                + (3.0 if exact_match else 0.0)
                + (cos_sim * 4.0 if cos_sim > 0 else 0.0)
            )
            if total_score > 0:
                scored.append((total_score, exact_match, index, event))

        if not scored and fallback:
            return events[-limit:]

        scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [event for _, _, _, event in scored[:limit]]

    def _save_embedding(self, event_id: str, content: str) -> None:
        if self.embedder is None:
            return
        try:
            vector = self.embedder.embed(content)
        except Exception:
            return
        if vector is None:
            return
        self.relational_dir.mkdir(parents=True, exist_ok=True)
        with self.embeddings_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event_id": event_id, "vector": vector}) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._mirror_embedding(event_id, vector)

    def _load_embeddings(self) -> dict[str, list[float]]:
        # The mirror may lag after a failure. Merge it with JSONL, then let the
        # append-only source of truth win if a record exists in both.
        mapping: dict[str, list[float]] = {}
        if self._mirror is not None:
            try:
                mapping.update(self._mirror.load_embedding_vectors())
            except Exception:
                _log.warning("mirror: failed to load embeddings", exc_info=True)
        if not self.embeddings_path.exists():
            return mapping
        for record in self._read_jsonl(self.embeddings_path):
            if isinstance(record, dict) and "event_id" in record and "vector" in record:
                mapping[record["event_id"]] = record["vector"]
        return mapping

    # -- fail-soft SQLite mirror writes --

    def _mirror_event(self, event: Event) -> None:
        if self._mirror is None:
            return
        try:
            self._mirror.execute(
                "INSERT OR IGNORE INTO events (id, role, content, said_at) VALUES (?, ?, ?, ?)",
                (event["id"], event["role"], event["content"], event["said_at"]),
            )
            self._mirror.execute(
                "INSERT OR IGNORE INTO event_sources (event_id, source) VALUES (?, ?)",
                (event["id"], event["source"]),
            )
            self._mirror.execute(
                "INSERT INTO sessions (id, created_at, last_active_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "created_at = MIN(created_at, excluded.created_at), "
                "last_active_at = MAX(last_active_at, excluded.last_active_at)",
                (event["session_id"], event["said_at"], event["said_at"]),
            )
            self._mirror.execute(
                "INSERT OR IGNORE INTO event_sessions (event_id, session_id) VALUES (?, ?)",
                (event["id"], event["session_id"]),
            )
            if "call_id" in event and "turn_id" in event:
                self._mirror.execute(
                    "INSERT OR IGNORE INTO voice_event_context (event_id, call_id, turn_id) VALUES (?, ?, ?)",
                    (event["id"], event["call_id"], event["turn_id"]),
                )
        except Exception:
            _log.warning("mirror: failed to write event %s", event.get("id"), exc_info=True)

    def _mirror_transcript_correction(self, record: TranscriptCorrection) -> None:
        if self._mirror is None:
            return
        try:
            self._mirror.execute(
                "INSERT OR IGNORE INTO transcript_corrections (id, source_event_id, content, said_at) VALUES (?, ?, ?, ?)",
                (record["id"], record["source_event_id"], record["content"], record["said_at"]),
            )
        except Exception:
            _log.warning("mirror: failed to write transcript correction %s", record["id"], exc_info=True)

    def _mirror_chat_boundary(self, record: ChatBoundary) -> None:
        if self._mirror is None:
            return
        try:
            self._mirror.execute(
                "INSERT OR IGNORE INTO chat_boundaries (said_at) VALUES (?)",
                (record["said_at"],),
            )
            if "session_id" in record:
                self._mirror.execute(
                    "INSERT OR IGNORE INTO sessions (id, created_at, last_active_at) VALUES (?, ?, ?)",
                    (record["session_id"], record["said_at"], record["said_at"]),
                )
        except Exception:
            _log.warning("mirror: failed to write chat boundary", exc_info=True)

    def _mirror_session_metadata(self, record: SessionMetadata) -> None:
        if self._mirror is None:
            return
        try:
            if "title" in record:
                self._mirror.execute("UPDATE sessions SET title = ? WHERE id = ?", (record["title"], record["session_id"]))
            if "archived" in record:
                self._mirror.execute(
                    "UPDATE sessions SET archived = ? WHERE id = ?",
                    (int(record["archived"]), record["session_id"]),
                )
        except Exception:
            _log.warning("mirror: failed to write session metadata %s", record["session_id"], exc_info=True)

    def _mirror_entity_observation(self, record: EntityObservation) -> None:
        if self._mirror is None:
            return
        try:
            self._mirror.execute(
                "INSERT OR IGNORE INTO entity_observations (entity_id, name, observation, said_at, source_event_id) VALUES (?, ?, ?, ?, ?)",
                (record["entity_id"], record["name"], record["observation"],
                 record["said_at"], record.get("source_event_id")),
            )
        except Exception:
            _log.warning("mirror: failed to write entity observation", exc_info=True)

    def _mirror_heartbeat_completion(self, record: HeartbeatCompletion) -> None:
        if self._mirror is None:
            return
        try:
            if record["stage"] == "metabolism":
                self._mirror.execute(
                    "INSERT OR IGNORE INTO metabolism_completions (source_event_id, said_at) VALUES (?, ?)",
                    (record["source_event_id"], record["said_at"]),
                )
            else:
                self._mirror.execute(
                    "INSERT OR IGNORE INTO heartbeat_completions (stage, source_event_id, said_at) VALUES (?, ?, ?)",
                    (record["stage"], record["source_event_id"], record["said_at"]),
                )
        except Exception:
            _log.warning("mirror: failed to write heartbeat completion", exc_info=True)

    def _mirror_search_record(self, record: SearchRecord) -> None:
        if self._mirror is None:
            return
        try:
            self._mirror.execute(
                "INSERT OR IGNORE INTO search_records (id, query, sources, origin, source_event_id, said_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record["id"],
                    record["query"],
                    json.dumps(record["sources"], ensure_ascii=False),
                    record["origin"],
                    record["source_event_id"],
                    record["said_at"],
                ),
            )
        except Exception:
            _log.warning("mirror: failed to write search record %s", record["id"], exc_info=True)

    def _mirror_embedding(self, event_id: str, vector: list[float]) -> None:
        if self._mirror is None:
            return
        try:
            self._mirror.store_embedding_vector(event_id, vector)
        except Exception:
            _log.warning("mirror: failed to write embedding %s", event_id, exc_info=True)

    def _append_record(self, record: object) -> None:
        self._append_records([record])

    def _append_records(self, records: list[object]) -> None:
        created = not self.path.exists()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.relational_dir.mkdir(parents=True, exist_ok=True)
        self.interior_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as events_file:
            events_file.write("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))
            events_file.flush()
            os.fsync(events_file.fileno())
        if created:
            self._fsync_directory(self.data_root)

    def _read_records(self) -> list[object]:
        return self._read_jsonl(self.path)

    @staticmethod
    def _read_jsonl(path: Path) -> list[object]:
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as events_file:
            lines = events_file.readlines()
        records: list[object] = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                if index == len(lines) - 1 and not line.endswith("\n"):
                    break
                raise
        return records

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _tokenize(text: str, *, keep_stop_words: bool = True) -> set[str]:
        tokens = {token for token in re.findall(r"[A-Za-z0-9']+", text.lower()) if token}
        if keep_stop_words:
            return tokens
        return tokens - _STOP_WORDS

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _parse_event(record: object, index: int, session_id: str) -> Event:
        if (
            not isinstance(record, dict)
            or record.get("role") not in {"user", "assistant"}
            or not isinstance(record.get("content"), str)
            or not isinstance(record.get("said_at"), str)
        ):
            raise ValueError("Invalid event record")
        event: Event = Event(
            role=record["role"],
            content=record["content"],
            said_at=record["said_at"],
            session_id=session_id,
        )
        if "id" in record:
            if not isinstance(record["id"], str):
                raise ValueError("Invalid event record")
            event["id"] = record["id"]
        else:
            event["id"] = f"legacy:{index}"
        if "source" in record:
            if record["source"] not in {"text", "voice"}:
                raise ValueError("Invalid event record")
            event["source"] = record["source"]
        if "call_id" in record or "turn_id" in record:
            if not isinstance(record.get("call_id"), str) or not isinstance(record.get("turn_id"), str):
                raise ValueError("Invalid event record")
            event["call_id"] = record["call_id"]
            event["turn_id"] = record["turn_id"]
        return event

    @staticmethod
    def _record_session_id(record: object, fallback: str) -> str:
        if not isinstance(record, dict) or "session_id" not in record:
            return fallback
        if not isinstance(record["session_id"], str) or not record["session_id"]:
            raise ValueError("Invalid session ID")
        return record["session_id"]

    @classmethod
    def _active_session_id(cls, records: list[object]) -> str:
        session_id = legacy_session_id(-1)
        found_boundary = False
        for index, record in enumerate(records):
            if isinstance(record, dict) and record.get("kind") == "chat_boundary":
                session_id = cls._record_session_id(record, legacy_session_id(index))
                found_boundary = True
            elif isinstance(record, dict) and record.get("kind") == "session_open":
                session_id = cls._record_session_id(record, session_id)
                found_boundary = True
            elif (
                not found_boundary
                and isinstance(record, dict)
                and record.get("role") in {"user", "assistant"}
                and "session_id" in record
            ):
                session_id = cls._record_session_id(record, session_id)
        return session_id

    @classmethod
    def _resumed_session(cls, records: list[object], active_session_id: str) -> str | None:
        session_id = legacy_session_id(-1)
        last_event_session_id: str | None = None
        selected_by_open = False
        for index, record in enumerate(records):
            if isinstance(record, dict) and record.get("kind") == "chat_boundary":
                session_id = cls._record_session_id(record, legacy_session_id(index))
                selected_by_open = False
            elif isinstance(record, dict) and record.get("kind") == "session_open":
                selected_by_open = cls._record_session_id(record, session_id) == active_session_id
            elif isinstance(record, dict) and record.get("role") in {"user", "assistant"}:
                last_event_session_id = cls._record_session_id(record, session_id)
                if selected_by_open and last_event_session_id == active_session_id:
                    selected_by_open = False
        return (
            active_session_id
            if selected_by_open and last_event_session_id and last_event_session_id != active_session_id
            else None
        )

    @staticmethod
    def _parse_transcript_correction(record: object) -> TranscriptCorrection:
        if (
            not isinstance(record, dict)
            or record.get("kind") != "transcript_correction"
            or not isinstance(record.get("id"), str)
            or not isinstance(record.get("source_event_id"), str)
            or not isinstance(record.get("content"), str)
            or not isinstance(record.get("said_at"), str)
        ):
            raise ValueError("Invalid transcript correction record")
        return TranscriptCorrection(
            kind="transcript_correction",
            id=record["id"],
            source_event_id=record["source_event_id"],
            content=record["content"],
            said_at=record["said_at"],
        )

    @staticmethod
    def _is_legacy_search_event(record: object) -> bool:
        if not isinstance(record, dict) or record.get("role") != "assistant":
            return False
        content = record.get("content")
        if not isinstance(content, str) or "\nSources:" not in content:
            return False
        return content.startswith(("[Web search: ", "[Metabolism search: "))
