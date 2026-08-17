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
    held_close: NotRequired[bool]


class PrivacyRecord(TypedDict):
    kind: Literal["privacy"]
    target_id: str
    held_close: bool
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
    def __init__(self, data_root: Path | None = None, embedder: EmbeddingClient | None = None) -> None:
        self.data_root = data_root or Path.home() / "sage_data"
        self.relational_dir = self.data_root / "relational"
        self.interior_dir = self.data_root / "interior"
        self.path = self.data_root / "events.jsonl"
        self.embeddings_path = self.relational_dir / "embeddings.jsonl"
        self.entities_path = self.relational_dir / "entities.jsonl"
        self.embedder = embedder

    def append(self, role: Literal["user", "assistant"], content: str) -> Event:
        event: Event = {
            "id": str(uuid4()),
            "role": role,
            "content": content,
            "said_at": self._timestamp(),
        }
        self._append_record(event)
        # Compute and persist embedding asynchronously or immediately if embedder is available
        if self.embedder is not None:
            self._save_embedding(event["id"], content)
        return event

    def append_privacy(
        self,
        target_id: str,
        held_close: bool,
        source: Literal["sensor", "user"],
        *,
        carry_after: int | None = None,
    ) -> PrivacyRecord:
        record: PrivacyRecord = {
            "kind": "privacy",
            "target_id": target_id,
            "held_close": held_close,
            "source": source,
            "said_at": self._timestamp(),
        }
        if carry_after is not None:
            record["carry_after"] = carry_after
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
        if not self.entities_path.exists():
            return []
        records: list[EntityObservation] = []
        with self.entities_path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if data.get("kind") == "entity_obs":
                        records.append(data)
                except json.JSONDecodeError:
                    continue
        return records

    def history(self) -> list[Event]:
        records = self._read_records()
        privacy = self._privacy_status(records)
        events: list[Event] = []
        for index, record in enumerate(records):
            if isinstance(record, dict) and record.get("kind") == "privacy":
                continue
            event = self._parse_event(record, index)
            event["held_close"] = privacy.get(event["id"], False)
            events.append(event)
        return events

    def read_all(self) -> list[Event]:
        return self.history()

    def set_held_close(self, event_id: str, held_close: bool) -> bool:
        for event in self.history():
            if event["id"] == event_id and event["role"] == "user":
                self.append_privacy(event_id, held_close, "user")
                return True
        return False

    def carry_before_next_user_event(self) -> int:
        carry = 0
        for record in self._read_records():
            if not isinstance(record, dict) or record.get("kind") != "privacy":
                continue
            privacy = self._parse_privacy(record)
            if privacy["source"] == "sensor":
                carry = privacy.get("carry_after", 0)
        return carry

    def recall(self, query: str, limit: int = 8, *, exclude_event_id: str | None = None) -> list[Event]:
        if limit <= 0:
            return []

        events = [event for event in self.history() if event["role"] in {"user", "assistant"} and not event["held_close"]]
        if exclude_event_id is not None:
            events = [event for event in events if event["id"] != exclude_event_id]

        if not events:
            return []

        if not query.strip():
            return events[-limit:]

        raw_terms = self._tokenize(query, keep_stop_words=True)
        query_terms = self._tokenize(query, keep_stop_words=len(raw_terms) <= 1)
        normalized_query = query.strip().lower()

        if not query_terms:
            return events[-limit:]

        # Check for vector embedding similarity if embedder available
        query_embedding: list[float] | None = None
        embeddings_map: dict[str, list[float]] = {}
        if self.embedder is not None:
            query_embedding = self.embedder.embed(query)
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

        if not scored:
            return events[-limit:]

        scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [event for _, _, _, event in scored[:limit]]

    def _save_embedding(self, event_id: str, content: str) -> None:
        if self.embedder is None:
            return
        vector = self.embedder.embed(content)
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
        with self.embeddings_path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    mapping[record["event_id"]] = record["vector"]
                except (json.JSONDecodeError, KeyError):
                    continue
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
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as events_file:
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
                status[privacy["target_id"]] = privacy["held_close"]
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
        return event

    @staticmethod
    def _parse_privacy(record: object) -> PrivacyRecord:
        if (
            not isinstance(record, dict)
            or record.get("kind") != "privacy"
            or not isinstance(record.get("target_id"), str)
            or not isinstance(record.get("held_close"), bool)
            or record.get("source") not in {"sensor", "user"}
            or not isinstance(record.get("said_at"), str)
        ):
            raise ValueError("Invalid privacy record")
        parsed: PrivacyRecord = PrivacyRecord(
            kind="privacy",
            target_id=record["target_id"],
            held_close=record["held_close"],
            source=record["source"],
            said_at=record["said_at"],
        )
        if "carry_after" in record:
            if not isinstance(record["carry_after"], int) or record["carry_after"] < 0:
                raise ValueError("Invalid privacy record")
            parsed["carry_after"] = record["carry_after"]
        return parsed
