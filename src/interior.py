"""Interior memory and reflection persistence for Sage."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict
from uuid import uuid4

if TYPE_CHECKING:
    from database import Database

_log = logging.getLogger(__name__)


class Reflection(TypedDict):
    id: str
    content: str
    said_at: str
    category: NotRequired[str]
    source_event_id: NotRequired[str]


class Belief(TypedDict):
    id: str
    topic: str
    stance: str
    evidence: str
    said_at: str
    revised_from: NotRequired[str]


IDENTITY_VERDICTS = ("ratified", "rejected", "retired")


class IdentityProposal(TypedDict):
    kind: Literal["proposal"]
    id: str
    claim: str
    evidence: list[str]
    said_at: str


class IdentityRuling(TypedDict):
    kind: Literal["ruling"]
    id: str
    target_id: str
    verdict: str
    said_at: str


class IdentityEntry(TypedDict):
    """A proposal folded together with the verdict of its latest ruling."""
    id: str
    claim: str
    evidence: list[str]
    said_at: str
    status: str


class WaitingMessage(TypedDict):
    content: str
    said_at: str
    revised_at: NotRequired[str]
    read: bool


class InteriorStore:
    def __init__(self, data_root: Path | None = None, *, mirror: Database | None = None) -> None:
        self.data_root = data_root or Path.home() / "sage_data"
        self.interior_dir = self.data_root / "interior"
        self.reflections_path = self.interior_dir / "reflections.jsonl"
        self.beliefs_path = self.interior_dir / "beliefs.jsonl"
        self.identity_path = self.interior_dir / "identity.jsonl"
        self.waiting_message_path = self.interior_dir / "waiting_message.json"
        self._mirror = mirror

    def _ensure_dir(self) -> None:
        self.interior_dir.mkdir(parents=True, exist_ok=True)

    def append_reflection(
        self,
        content: str,
        category: str = "general",
        *,
        source_event_id: str | None = None,
    ) -> Reflection:
        self._ensure_dir()
        if source_event_id is not None:
            for existing in self.list_reflections(limit=10_000):
                if existing.get("source_event_id") == source_event_id:
                    return existing
        reflection: Reflection = {
            "id": str(uuid4()),
            "content": content,
            "said_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "category": category,
        }
        if source_event_id is not None:
            reflection["source_event_id"] = source_event_id
        with self.reflections_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(reflection, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._mirror_reflection(reflection)
        return reflection

    def list_reflections(self, limit: int = 20) -> list[Reflection]:
        if not self.reflections_path.exists():
            return []
        reflections: list[Reflection] = []
        reflections = [record for record in self._read_jsonl(self.reflections_path) if isinstance(record, dict)]
        return reflections[-limit:]

    def has_reflection_for_source(self, source_event_id: str) -> bool:
        return any(
            isinstance(record, dict) and record.get("source_event_id") == source_event_id
            for record in self._read_jsonl(self.reflections_path)
        )

    # -- self-authored identity: proposals Elliot rules on, folded at read time --

    def append_identity_proposal(self, claim: str, evidence: list[str]) -> IdentityProposal:
        if not claim.strip():
            raise ValueError("identity proposal needs a claim")
        proposal: IdentityProposal = {
            "kind": "proposal",
            "id": str(uuid4()),
            "claim": claim.strip(),
            "evidence": list(evidence),
            "said_at": self._timestamp(),
        }
        self._append_identity(proposal)
        return proposal

    def append_identity_ruling(self, target_id: str, verdict: str) -> IdentityRuling:
        if verdict not in IDENTITY_VERDICTS:
            raise ValueError(f"unknown identity verdict: {verdict!r}")
        ruling: IdentityRuling = {
            "kind": "ruling",
            "id": str(uuid4()),
            "target_id": target_id,
            "verdict": verdict,
            "said_at": self._timestamp(),
        }
        self._append_identity(ruling)
        return ruling

    def list_identity(self) -> list[IdentityEntry]:
        """Proposals in file order, each carrying its latest verdict. Records stay intact."""
        records = [r for r in self._read_jsonl(self.identity_path) if isinstance(r, dict)]
        verdicts = {
            r["target_id"]: r["verdict"]
            for r in records
            if r.get("kind") == "ruling" and r.get("target_id") and r.get("verdict")
        }
        return [
            {
                "id": r["id"],
                "claim": r.get("claim", ""),
                "evidence": r.get("evidence") or [],
                "said_at": r.get("said_at", ""),
                "status": verdicts.get(r["id"], "proposed"),
            }
            for r in records
            if r.get("kind") == "proposal" and r.get("id")
        ]

    def _append_identity(self, record: IdentityProposal | IdentityRuling) -> None:
        self._ensure_dir()
        with self.identity_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._mirror_identity(record)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def list_beliefs(self) -> list[Belief]:
        if not self.beliefs_path.exists():
            return []
        beliefs: list[Belief] = []
        beliefs = [record for record in self._read_jsonl(self.beliefs_path) if isinstance(record, dict)]
        return beliefs

    @staticmethod
    def _read_jsonl(path: Path) -> list[object]:
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as data_file:
            lines = data_file.readlines()
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
        self._mirror_waiting_message(msg)
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
        self._mirror_clear_waiting_message()

    # -- fail-soft SQLite mirror writes --

    def _mirror_reflection(self, reflection: Reflection) -> None:
        if self._mirror is None:
            return
        try:
            self._mirror.execute(
                "INSERT OR IGNORE INTO reflections (id, content, said_at, category, source_event_id) VALUES (?, ?, ?, ?, ?)",
                (reflection["id"], reflection["content"], reflection["said_at"],
                 reflection.get("category", "general"), reflection.get("source_event_id")),
            )
        except Exception:
            _log.warning("mirror: failed to write reflection %s", reflection.get("id"), exc_info=True)

    def _mirror_identity(self, record: IdentityProposal | IdentityRuling) -> None:
        if self._mirror is None:
            return
        data = dict(record)
        try:
            self._mirror.execute(
                "INSERT OR IGNORE INTO identity_entries (id, kind, claim, evidence, target_id, verdict, said_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (data["id"], data["kind"], data.get("claim"),
                 json.dumps(data["evidence"]) if "evidence" in data else None,
                 data.get("target_id"), data.get("verdict"), data["said_at"]),
            )
        except Exception:
            _log.warning("mirror: failed to write identity entry %s", data.get("id"), exc_info=True)

    def _mirror_waiting_message(self, msg: WaitingMessage) -> None:
        if self._mirror is None:
            return
        try:
            self._mirror.execute(
                "INSERT OR REPLACE INTO waiting_message (id, content, said_at, revised_at, read) VALUES (1, ?, ?, ?, ?)",
                (msg["content"], msg["said_at"], msg.get("revised_at"), int(msg.get("read", False))),
            )
        except Exception:
            _log.warning("mirror: failed to write waiting message", exc_info=True)

    def _mirror_clear_waiting_message(self) -> None:
        if self._mirror is None:
            return
        try:
            self._mirror.execute(
                "UPDATE waiting_message SET read = 1 WHERE id = 1",
            )
        except Exception:
            _log.warning("mirror: failed to clear waiting message", exc_info=True)
