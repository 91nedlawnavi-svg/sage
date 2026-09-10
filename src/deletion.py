"""Preview-bound, fail-closed permanent session deletion.

Prepare survivor-only files and mirrors, then durably mark the decision. Before
that marker no original changes; after it recovery rolls forward under the same
cross-process gate before any Sage reader, writer or provider work may proceed.
No backup containing the removed conversation is created.
"""

from dataclasses import dataclass, field
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from database import relational_db, interior_db as open_interior_db
from events import EventStore, legacy_session_id, record_digest
from interior import InteriorStore
from persistence import activity_gate, data_gate, guarded, RecoveryRequired


class DeletionError(ValueError):
    pass


FILES = {
    "events": "events.jsonl",
    "entities": "relational/entities.jsonl",
    "heartbeat": "relational/heartbeat.jsonl",
    "searches": "relational/searches.jsonl",
    "embeddings": "relational/embeddings.jsonl",
    "reflections": "interior/reflections.jsonl",
    "metabolism": "interior/metabolism.jsonl",
    "identity": "interior/identity.jsonl",
    "waiting_message": "interior/waiting_message.json",
    "legacy_ids": "relational/legacy_ids.json",
}
DATABASES = ("relational/relational.db", "interior/interior.db")
TRANSACTION = ".session-deletion"


@dataclass
class DeletionPlan:
    session_id: str
    title: str
    event_ids: set[str]
    paths: dict[str, set[int]]
    unresolved: list[str]
    revision: str
    snapshots: dict[str, bytes | None] = field(repr=False)

    @property
    def counts(self):
        return {name: len(ids) for name, ids in self.paths.items() if ids}

    def as_dict(self):
        return {
            "session_id": self.session_id, "title": self.title,
            "counts": self.counts, "event_ids": sorted(self.event_ids),
            "unresolved": self.unresolved, "revision": self.revision,
            "backup_disclosure": (
                "Permanently removes the listed records from Sage's active local files and SQLite mirrors. "
                "Other chats are retained and may repeat this conversation. External backups, provider-held "
                "context and already-open browser or voice sessions are not erased. This is not forensic disk erasure."
            ),
        }


def _snapshot(root):
    result = {}
    for relative in DATABASES:
        path = root / relative
        if path.parent.is_symlink() or any(Path(str(path) + suffix).is_symlink() for suffix in ("", "-wal", "-shm", "-journal")):
            raise DeletionError("Deletion does not support symlinked SQLite stores")
    for name, relative in FILES.items():
        path = root / relative
        if path.is_symlink() or path.parent.is_symlink():
            raise DeletionError("Deletion does not support symlinked data stores")
        result[name] = path.read_bytes() if path.exists() else None
        if path.with_name(f".{path.name}.delete-tmp").exists():
            raise DeletionError("An older deletion staging file needs inspection before deletion")
    return result


def _records(data, name):
    if data is None:
        return []
    try:
        if name in {"waiting_message", "legacy_ids"}:
            values = [json.loads(data)]
            if name == "waiting_message" and values == [{}]:
                return []
        else:
            values = [json.loads(line) for line in data.splitlines() if line.strip()]
        if not all(isinstance(row, dict) for row in values):
            raise ValueError("non-object record")
        return values
    except (ValueError, UnicodeError) as exc:
        raise DeletionError(f"{name} contains incomplete or invalid data; nothing was deleted") from exc


def _revision(snapshots, session_id):
    digest = hashlib.sha256(session_id.encode())
    for name, data in sorted(snapshots.items()):
        digest.update(name.encode() + b"\0")
        digest.update(b"missing" if data is None else hashlib.sha256(data).digest())
    return digest.hexdigest()


@guarded(lambda store, interior, session_id: store.data_root)
def build_deletion_plan(store, interior, session_id):
    if Path(store.data_root).resolve() != Path(interior.data_root).resolve():
        raise DeletionError("Relational and interior stores must share one data root")
    snapshots = _snapshot(store.data_root)
    rows = {name: _records(data, name) for name, data in snapshots.items()}
    summary = store._session_summary(session_id)
    paths = {name: set() for name in FILES if name != "legacy_ids"}
    paths["session_controls"] = set()
    paths["transcript_corrections"] = set()
    event_ids = set()
    unresolved = []
    active = legacy_session_id(-1)
    for index, row in enumerate(store._read_records()):
        kind = row.get("kind")
        if kind == "chat_boundary":
            active = store._record_session_id(row, legacy_session_id(index))
            if active == session_id:
                paths["session_controls"].add(index)
        elif kind in {"session_metadata", "session_open"}:
            if row.get("session_id") == session_id:
                paths["session_controls"].add(index)
        elif row.get("role") in {"user", "assistant"}:
            if store._record_session_id(row, active) == session_id:
                paths["events"].add(index)
                event_ids.add(row.get("id", f"legacy:{index}"))
        elif kind not in {"transcript_correction", "privacy"}:
            unresolved.append("events contains an unsupported record type")
    for index, row in enumerate(rows["events"]):
        if row.get("kind") in {"transcript_correction", "privacy"}:
            source = row.get("source_event_id") or row.get("target_id") or row.get("event_id")
            if not source:
                unresolved.append("event control has unknown provenance")
            elif source in event_ids:
                paths["transcript_corrections"].add(index)

    removed_proposals = set()
    all_event_ids = {
        row.get("id", f"legacy:{index}")
        for index, row in enumerate(store._read_records())
        if row.get("role") in {"user", "assistant"}
    }
    for name in ("entities", "heartbeat", "embeddings", "searches", "reflections", "metabolism", "waiting_message", "identity"):
        for index, row in enumerate(rows[name]):
            if name == "identity" and row.get("kind") == "ruling":
                continue
            if name in {"entities", "heartbeat", "embeddings"}:
                source = row.get("event_id" if name == "embeddings" else "source_event_id")
                sources, complete = {source} if isinstance(source, str) and source else set(), bool(source)
            else:
                proof = row.get("provenance", {})
                sources = proof.get("event_ids") if isinstance(proof, dict) else None
                valid = isinstance(sources, list) and all(isinstance(s, str) and s for s in sources)
                complete = valid and proof.get("version") == 1 and proof.get("complete") is True
                sources = set(sources) if valid else set()
            if not complete:
                unresolved.append(f"{name} record {index + 1} has incomplete provenance")
            elif sources - all_event_ids:
                unresolved.append(f"{name} record {index + 1} references missing source events")
            elif sources & event_ids:
                if sources - event_ids:
                    unresolved.append(f"{name} record {index + 1} also depends on another chat")
                else:
                    paths[name].add(index)
                    if name == "identity":
                        removed_proposals.add(row.get("id"))
    for index, row in enumerate(rows["identity"]):
        if row.get("kind") == "ruling" and row.get("target_id") in removed_proposals:
            paths["identity"].add(index)
    return DeletionPlan(session_id, summary["title"], event_ids, paths,
                        list(dict.fromkeys(unresolved)), _revision(snapshots, session_id), snapshots)


def _survivors(data, remove):
    if data is None:
        return None
    output = []
    index = 0
    for line in data.splitlines(keepends=True):
        if not line.strip():
            output.append(line)
            continue
        if index not in remove:
            output.append(line)
        index += 1
    return b"".join(output)


def _legacy_survivors(store, plan, remove):
    raw = _records(plan.snapshots["events"], "events")
    resolved = store._read_records()
    active = legacy_session_id(-1)
    mapping = {}
    position = 0
    for index, row in enumerate(resolved):
        identity = {}
        if row.get("kind") == "chat_boundary":
            active = store._record_session_id(row, legacy_session_id(index))
            if not raw[index].get("session_id"):
                identity["session_id"] = active
        elif row.get("role") in {"user", "assistant"}:
            if not raw[index].get("id"):
                identity["id"] = row.get("id", f"legacy:{index}")
            if not raw[index].get("session_id"):
                identity["session_id"] = store._record_session_id(row, active)
        if index not in remove:
            if identity:
                mapping[str(position)] = {"digest": record_digest(raw[index]), "identity": identity}
            position += 1
    return json.dumps({"version": 1, "generation": str(uuid4()), "records": mapping}).encode()


def _fsync(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as stream:
        os.fchmod(stream.fileno(), mode & 0o777)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    _fsync(path.parent)


def _restore_mirror(source, target):
    """SQLite backup is transactional; keep target inode for existing connections."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.parent.is_symlink():
        raise RecoveryRequired("Deletion mirror target changed to a symlink")
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        dst.execute("PRAGMA secure_delete=ON")
        src.backup(dst)
        dst.execute("VACUUM")
        busy, _, _ = dst.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise RecoveryRequired("SQLite checkpoint is busy; deletion recovery must finish")


def recover_deletion(root):
    """Called only while the root gate is held, including on process restart."""
    root = Path(root)
    stage = root / TRANSACTION
    marker = stage / "COMMIT.json"
    if marker.exists():
        manifest = json.loads(marker.read_bytes())
        for relative in manifest["files"]:
            if relative not in FILES.values():
                raise RecoveryRequired("Invalid deletion recovery manifest")
            source, target = stage / relative, root / relative
            expected = manifest["digests"][relative]
            candidate = source if source.exists() else target
            if not candidate.exists() or hashlib.sha256(candidate.read_bytes()).hexdigest() != expected:
                raise RecoveryRequired("A deletion survivor file is missing or damaged")
            if source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                _fsync(target.parent)
            elif not target.exists():
                raise RecoveryRequired("A deletion survivor file is missing")
        for relative in manifest["databases"]:
            if relative not in DATABASES:
                raise RecoveryRequired("Invalid deletion mirror path")
            source = stage / relative
            if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != manifest["digests"][relative]:
                raise RecoveryRequired("A deletion mirror snapshot is missing or damaged")
            _restore_mirror(stage / relative, root / relative)
        # Removing the durable decision is the very last commit step. Leftover
        # survivor-only files can be discarded after a crash during cleanup.
        marker.unlink()
        _fsync(stage)
    shutil.rmtree(stage)
    _fsync(root)


def execute_deletion(store, interior, relational, interior_db, plan, confirmation, *, before_commit=None):
    if confirmation != "DELETE":
        raise DeletionError("Type DELETE exactly to permanently delete this chat")
    with activity_gate(store.data_root, exclusive=True), data_gate(store.data_root):
        current = build_deletion_plan(store, interior, plan.session_id)
        if current.revision != plan.revision:
            raise DeletionError("Deletion preview changed. Review a fresh preview and confirm again.")
        if current.unresolved:
            raise DeletionError("Deletion blocked: " + "; ".join(current.unresolved[:4]))
        # Never trust mutable caller-supplied removal indexes.
        plan = current
        root = Path(store.data_root)
        stage = root / TRANSACTION
        stage.mkdir(mode=0o700)
        committed = False
        try:
            _fsync(root)
            remove = plan.paths["events"] | plan.paths["session_controls"] | plan.paths["transcript_corrections"]
            manifest = {"files": [], "databases": list(DATABASES), "digests": {}}
            for name, relative in FILES.items():
                data = plan.snapshots[name]
                if name == "legacy_ids":
                    data = _legacy_survivors(store, plan, remove)
                elif name == "waiting_message":
                    if plan.paths[name]:
                        data = b"{}"
                else:
                    data = _survivors(data, remove if name == "events" else plan.paths[name])
                if name == "events" and store.current_session_id == plan.session_id:
                    boundary = {"kind": "chat_boundary", "session_id": str(uuid4()), "said_at": store._timestamp()}
                    data = (data or b"")
                    if data and not data.endswith(b"\n"):
                        data += b"\n"
                    data += (json.dumps(boundary) + "\n").encode()
                if data is not None:
                    original = root / relative
                    mode = original.stat().st_mode if original.exists() else 0o600
                    _write(stage / relative, data, mode)
                    manifest["files"].append(relative)
                    manifest["digests"][relative] = hashlib.sha256(data).hexdigest()
            from mirror_rebuild import backfill_relational, backfill_interior
            rel, intr = relational_db(stage), open_interior_db(stage)
            try:
                backfill_relational(rel, stage)
                backfill_interior(intr, stage)
            finally:
                rel.close()
                intr.close()
            for relative in DATABASES:
                _fsync(stage / relative)
                _fsync((stage / relative).parent)
                manifest["digests"][relative] = hashlib.sha256((stage / relative).read_bytes()).hexdigest()
            if before_commit:
                before_commit()
            # Also catches reentrant writes from callbacks before the decision.
            if _revision(_snapshot(root), plan.session_id) != plan.revision:
                raise DeletionError("Deletion preview changed. Review a fresh preview and confirm again.")
            _write(stage / "decision.tmp", json.dumps(manifest).encode())
            os.replace(stage / "decision.tmp", stage / "COMMIT.json")
            committed = True
            _fsync(stage)
            recover_deletion(root)
        except Exception as exc:
            if committed:
                raise RecoveryRequired("Deletion was committed but recovery is pending. Data access is paused until recovery succeeds.") from exc
            if stage.exists():
                shutil.rmtree(stage)
                _fsync(root)
            raise
        store._sync_generation()
        return plan.as_dict()
