"""Append-only timestamped event persistence with vector embedding support for Sage."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
import re
from pathlib import Path
from typing import Literal, NotRequired, TypedDict
from uuid import uuid4

from router import EmbeddingClient

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
    sensitive: NotRequired[bool]
    provider_excluded: NotRequired[bool]
    privacy_carry_after: NotRequired[int]


class PrivacyRecord(TypedDict):
    kind: Literal["privacy"]
    target_id: str
    sensitive: bool
    source: Literal["sensor", "user"]
    carry_after: NotRequired[int]
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
    stage: Literal["entities", "reflection"]
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


def _read_sensitive(record: dict) -> object:
    """Read the sensitive flag, tolerating records written under the old `held_close` key."""
    return record["sensitive"] if "sensitive" in record else record.get("held_close")


class EventStore:
    def __init__(self, data_root: Path | None = None, embedder: EmbeddingClient | None = None) -> None:
        self.data_root = data_root or Path.home() / "sage_data"
        self.relational_dir = self.data_root / "relational"
        self.interior_dir = self.data_root / "interior"
        self.path = self.data_root / "events.jsonl"
        self.embeddings_path = self.relational_dir / "embeddings.jsonl"
        self.entities_path = self.relational_dir / "entities.jsonl"
        self.heartbeat_path = self.relational_dir / "heartbeat.jsonl"
        self.embedder = embedder

    def append(
        self,
        role: Literal["user", "assistant"],
        content: str,
        *,
        save_embedding: bool = True,
        initial_sensitive: bool | None = None,
        privacy_carry_after: int | None = None,
    ) -> Event:
        event: Event = {
            "id": str(uuid4()),
            "role": role,
            "content": content,
            "said_at": self._timestamp(),
        }
        if role == "user":
            event["provider_excluded"] = initial_sensitive is None or initial_sensitive
            if event["provider_excluded"]:
                save_embedding = False
            if initial_sensitive is not None:
                event["sensitive"] = initial_sensitive
                if privacy_carry_after is not None:
                    event["privacy_carry_after"] = privacy_carry_after
        elif initial_sensitive is not None:
            raise ValueError("Only user events can have privacy classification")
        self._append_record(event)
        if save_embedding and self.embedder is not None and not event.get("provider_excluded", False):
            try:
                self._save_embedding(event["id"], content)
            except OSError:
                pass
        return event

    def append_privacy(
        self,
        target_id: str,
        sensitive: bool,
        source: Literal["sensor", "user"],
        *,
        carry_after: int | None = None,
    ) -> PrivacyRecord:
        record: PrivacyRecord = {
            "kind": "privacy",
            "target_id": target_id,
            "sensitive": sensitive,
            "source": source,
            "said_at": self._timestamp(),
        }
        if carry_after is not None:
            record["carry_after"] = carry_after
        self._append_record(record)
        return record

    def append_chat_boundary(self) -> ChatBoundary:
        record: ChatBoundary = {"kind": "chat_boundary", "said_at": self._timestamp()}
        self._append_record(record)
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
        return record

    def entity_observations(self) -> list[EntityObservation]:
        return [
            record for record in self._read_jsonl(self.entities_path)
            if isinstance(record, dict) and record.get("kind") == "entity_obs"
        ]

    def append_heartbeat_completion(
        self,
        stage: Literal["entities", "reflection"],
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
        return record

    def heartbeat_completed(self, stage: Literal["entities", "reflection"]) -> set[str]:
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

    def history(self) -> list[Event]:
        records = self._read_records()
        privacy = self._privacy_status(records)
        sensitive_ids = {
            record["target_id"]
            for record in records
            if isinstance(record, dict)
            and record.get("kind") == "privacy"
            and _read_sensitive(record) is True
        }
        events: list[Event] = []
        for index, record in enumerate(records):
            if isinstance(record, dict) and record.get("kind") in {"privacy", "chat_boundary"}:
                continue
            event = self._parse_event(record, index)
            event["sensitive"] = privacy.get(event["id"], event.get("sensitive", False))
            event["provider_excluded"] = (
                event.get("provider_excluded", event["role"] == "user")
                or event["id"] in sensitive_ids
            )
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

    def set_sensitive(self, event_id: str, sensitive: bool) -> bool:
        for event in self.history():
            if event["id"] == event_id and event["role"] == "user":
                self.append_privacy(event_id, sensitive, "user")
                return True
        return False

    def carry_before_next_user_event(self) -> int:
        carry = 0
        for record in self._read_records():
            if isinstance(record, dict) and record.get("kind") == "privacy":
                privacy = self._parse_privacy(record)
                if privacy["source"] == "sensor":
                    carry = privacy.get("carry_after", 0)
            elif isinstance(record, dict) and record.get("role") == "user":
                carry = record.get("privacy_carry_after", carry)
        return carry

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
            and not event["sensitive"]
            and not event.get("provider_excluded", False)
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

        # Hybrid scoring: vector cosine similarity + BM25 term frequency + exact match bonus
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

    def _load_embeddings(self) -> dict[str, list[float]]:
        if not self.embeddings_path.exists():
            return {}
        mapping: dict[str, list[float]] = {}
        for record in self._read_jsonl(self.embeddings_path):
            if isinstance(record, dict) and "event_id" in record and "vector" in record:
                mapping[record["event_id"]] = record["vector"]
        return mapping

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
    def _privacy_status(records: list[object]) -> dict[str, bool]:
        status: dict[str, bool] = {}
        for record in records:
            if isinstance(record, dict) and record.get("kind") == "privacy":
                privacy = EventStore._parse_privacy(record)
                status[privacy["target_id"]] = privacy["sensitive"]
        return status

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
        if isinstance(_read_sensitive(record), bool):
            event["sensitive"] = bool(_read_sensitive(record))
        if isinstance(record.get("provider_excluded"), bool):
            event["provider_excluded"] = record["provider_excluded"]
        if isinstance(record.get("privacy_carry_after"), int) and record["privacy_carry_after"] >= 0:
            event["privacy_carry_after"] = record["privacy_carry_after"]
        return event

    @staticmethod
    def _parse_privacy(record: object) -> PrivacyRecord:
        if (
            not isinstance(record, dict)
            or record.get("kind") != "privacy"
            or not isinstance(record.get("target_id"), str)
            or not isinstance(_read_sensitive(record), bool)
            or record.get("source") not in {"sensor", "user"}
            or not isinstance(record.get("said_at"), str)
        ):
            raise ValueError("Invalid privacy record")
        parsed: PrivacyRecord = PrivacyRecord(
            kind="privacy",
            target_id=record["target_id"],
            sensitive=bool(_read_sensitive(record)),
            source=record["source"],
            said_at=record["said_at"],
        )
        if "carry_after" in record:
            if not isinstance(record["carry_after"], int) or record["carry_after"] < 0:
                raise ValueError("Invalid privacy record")
            parsed["carry_after"] = record["carry_after"]
        return parsed
