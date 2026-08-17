"""Interior memory and reflection persistence for Sage."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Literal, NotRequired, TypedDict
from uuid import uuid4


class Reflection(TypedDict):
    id: str
    content: str
    said_at: str
    category: NotRequired[str]


class Belief(TypedDict):
    id: str
    topic: str
    stance: str
    evidence: str
    said_at: str
    revised_from: NotRequired[str]


class WaitingMessage(TypedDict):
    content: str
    said_at: str
    revised_at: NotRequired[str]
    read: bool


class InteriorStore:
    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = data_root or Path.home() / "sage_data"
        self.interior_dir = self.data_root / "interior"
        self.reflections_path = self.interior_dir / "reflections.jsonl"
        self.beliefs_path = self.interior_dir / "beliefs.jsonl"
        self.waiting_message_path = self.interior_dir / "waiting_message.json"

    def _ensure_dir(self) -> None:
        self.interior_dir.mkdir(parents=True, exist_ok=True)

    def append_reflection(self, content: str, category: str = "general") -> Reflection:
        self._ensure_dir()
        reflection: Reflection = {
            "id": str(uuid4()),
            "content": content,
            "said_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "category": category,
        }
        with self.reflections_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(reflection, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return reflection

    def list_reflections(self, limit: int = 20) -> list[Reflection]:
        if not self.reflections_path.exists():
            return []
        reflections: list[Reflection] = []
        with self.reflections_path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    reflections.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return reflections[-limit:]

    def append_belief(
        self,
        topic: str,
        stance: str,
        evidence: str,
        *,
        revised_from: str | None = None,
    ) -> Belief:
        self._ensure_dir()
        belief: Belief = {
            "id": str(uuid4()),
            "topic": topic,
            "stance": stance,
            "evidence": evidence,
            "said_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if revised_from is not None:
            belief["revised_from"] = revised_from
        with self.beliefs_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(belief, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return belief

    def list_beliefs(self) -> list[Belief]:
        if not self.beliefs_path.exists():
            return []
        beliefs: list[Belief] = []
        with self.beliefs_path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    beliefs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return beliefs

    def get_waiting_message(self) -> WaitingMessage | None:
        if not self.waiting_message_path.exists():
            return None
        try:
            with self.waiting_message_path.open(encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "content" in data and not data.get("read", False):
                return data  # type: ignore[return-value]
        except (json.JSONDecodeError, OSError):
            return None
        return None

    def set_waiting_message(self, content: str) -> WaitingMessage:
        self._ensure_dir()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        existing = self.get_waiting_message()
        msg: WaitingMessage = {
            "content": content,
            "said_at": existing["said_at"] if existing else now,
            "read": False,
        }
        if existing:
            msg["revised_at"] = now
        with self.waiting_message_path.open("w", encoding="utf-8") as f:
            json.dump(msg, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        return msg

    def clear_waiting_message(self) -> None:
        if self.waiting_message_path.exists():
            try:
                with self.waiting_message_path.open("r+", encoding="utf-8") as f:
                    data = json.load(f)
                    data["read"] = True
                    f.seek(0)
                    f.truncate()
                    json.dump(data, f, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
            except (json.JSONDecodeError, OSError):
                try:
                    self.waiting_message_path.unlink(missing_ok=True)
                except OSError:
                    pass
