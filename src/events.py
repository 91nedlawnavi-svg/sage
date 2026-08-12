"""Append-only timestamped event persistence for Sage."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Literal, TypedDict


class Event(TypedDict):
    role: Literal["user", "assistant"]
    content: str
    said_at: str


class EventStore:
    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = data_root or Path.home() / "sage_data"
        self.path = self.data_root / "events.jsonl"

    def append(self, role: Literal["user", "assistant"], content: str) -> Event:
        event: Event = {
            "role": role,
            "content": content,
            "said_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self.data_root.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as events_file:
            events_file.write(json.dumps(event, ensure_ascii=False) + "\n")
            events_file.flush()
            os.fsync(events_file.fileno())
        return event

    def read_all(self) -> list[Event]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as events_file:
            return [self._parse(line) for line in events_file if line.strip()]

    @staticmethod
    def _parse(line: str) -> Event:
        record = json.loads(line)
        if (
            not isinstance(record, dict)
            or record.get("role") not in {"user", "assistant"}
            or not isinstance(record.get("content"), str)
            or not isinstance(record.get("said_at"), str)
        ):
            raise ValueError("Invalid event record")
        return Event(role=record["role"], content=record["content"], said_at=record["said_at"])
