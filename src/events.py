"""Append-only timestamped event persistence with vector embedding support for Sage."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict
from uuid import uuid4

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

    def append(
        self,
        role: Literal["user", "assistant"],
        content: str,
        *,
        save_embedding: bool = True,
        source: Literal["text", "voice"] = "text",
        call_id: str | None = None,
        turn_id: str | None = None,
    ) -> Event:
        if (call_id is None) != (turn_id is None) or (call_id is not None and source != "voice"):
            raise ValueError("Call context requires a voice event with both call and turn IDs")
        event: Event = {
            "id": str(uuid4()),
            "role": role,
            "content": content,
            "said_at": self._timestamp(),
            "source": source,
        }
        if call_id is not None and turn_id is not None:
            event["call_id"] = call_id
            event["turn_id"] = turn_id
        self._append_record(event)
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
        record: ChatBoundary = {"kind": "chat_boundary", "said_at": self._timestamp()}
        self._append_record(record)
        self._mirror_chat_boundary(record)
        return record

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
        for index, record in enumerate(records):
            if isinstance(record, dict) and record.get("kind") in {"privacy", "chat_boundary", "transcript_correction"}:
                continue
            if self._is_legacy_search_event(record):
                continue
            event = self._parse_event(record, index)
            corrected = corrections.get(event["id"]) if event.get("source") == "voice" else None
            if corrected is not None:
                event["original_content"] = event["content"]
                event["content"] = corrected
            events.append(event)
        return events

    def read_all(self) -> list[Event]:
        return self.history()

    def visible_history(self) -> list[Event]:
        records = self._read_records()
        boundary_index = max(
            (index for index, record in enumerate(records) if isinstance(record, dict) and record.get("kind") == "chat_boundary"),
            default=-1,
        )
        visible_ids = {
            record.get("id", f"legacy:{index}")
            for index, record in enumerate(records)
            if index > boundary_index and isinstance(record, dict) and record.get("role") in {"user", "assistant"}
        }
        return [event for event in self.history() if event["id"] in visible_ids]

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
        except Exception:
            _log.warning("mirror: failed to write chat boundary", exc_info=True)

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
        created = not self.path.exists()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.relational_dir.mkdir(parents=True, exist_ok=True)
        self.interior_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as events_file:
            events_file.write(json.dumps(record, ensure_ascii=False) + "\n")
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
    def _parse_event(record: object, index: int) -> Event:
        if (
            not isinstance(record, dict)
            or record.get("role") not in {"user", "assistant"}
            or not isinstance(record.get("content"), str)
            or not isinstance(record.get("said_at"), str)
        ):
            raise ValueError("Invalid event record")
        event: Event = Event(role=record["role"], content=record["content"], said_at=record["said_at"])
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
