"""Append-only timestamped event persistence for Sage."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
from typing import Literal, NotRequired, TypedDict
from uuid import uuid4


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


class EventStore:
    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = data_root or Path.home() / "sage_data"
        self.path = self.data_root / "events.jsonl"

    def append(self, role: Literal["user", "assistant"], content: str) -> Event:
        event: Event = {
            "id": str(uuid4()),
            "role": role,
            "content": content,
            "said_at": self._timestamp(),
        }
        self._append_record(event)
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

        scored: list[tuple[int, int, int, Event]] = []
        for index, event in enumerate(events):
            event_content = event["content"].lower()
            score = len(query_terms & self._tokenize(event_content, keep_stop_words=False))
            exact_match = 1 if normalized_query and normalized_query in event_content else 0
            if score > 0 or exact_match:
                scored.append((score, exact_match, index, event))

        if not scored:
            return events[-limit:]

        scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        selected = sorted(scored[:limit], key=lambda item: item[2])
        return [event for _, _, _, event in selected]

    def _append_record(self, record: object) -> None:
        created = not self.path.exists()
        self.data_root.mkdir(parents=True, exist_ok=True)
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
