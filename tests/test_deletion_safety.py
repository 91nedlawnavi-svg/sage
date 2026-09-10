"""Destructive probes use isolated temporary roots only, never lived Sage data."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from database import relational_db, interior_db
import deletion
from deletion import build_deletion_plan, execute_deletion, DeletionError
from events import EventStore, legacy_session_id
from interior import InteriorStore
from persistence import activity_gate, RecoveryRequired
from provenance import provenance
from mirror_rebuild import backfill_relational
from web import SageServer
from heartbeat import Heartbeat
from router import RouterResult
from metabolism import digest, reach


class DeletionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rel, self.intr = relational_db(self.root), interior_db(self.root)
        self.store = EventStore(self.root, mirror=self.rel)
        self.interior = InteriorStore(self.root, mirror=self.intr)

    def tearDown(self):
        # A failed probe may intentionally leave recovery pending; remove the
        # injected failure before closing databases (which also uses the gate).
        self.rel.close()
        self.intr.close()
        self.temp.cleanup()

    def pair(self):
        old = self.store.append("user", "DELETE_ME_UNIQUE", save_embedding=False)
        self.store.append_chat_boundary()
        keep = self.store.append("user", "KEEP_ME_UNIQUE", save_embedding=False)
        return old, keep

    def plan(self, event):
        return build_deletion_plan(self.store, self.interior, event["session_id"])

    def delete(self, plan, **kwargs):
        return execute_deletion(self.store, self.interior, self.rel, self.intr, plan, "DELETE", **kwargs)

    @contextmanager
    def server(self):
        router = Mock(aliases=("test",))
        server = SageServer(("127.0.0.1", 0), self.store, router, self.interior, self.rel, self.intr)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, body=None):
            req = Request(f"http://127.0.0.1:{server.server_port}{path}",
                data=json.dumps(body).encode() if body is not None else None,
                headers={"Content-Type": "application/json"})
            try:
                response = urlopen(req, timeout=5)
            except HTTPError as exc:
                response = exc
            with response:
                return response.status, json.load(response)
        try:
            yield request
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_http_requires_preview_revision_and_reports_sqlite_failure(self):
        old, keep = self.pair()
        with self.server() as request:
            status, preview = request(f"/api/sessions/deletion-preview?session_id={old['session_id']}&extra=1")
            self.assertEqual(status, 200)
            body = {"session_id": old["session_id"], "confirmation": "DELETE"}
            self.assertEqual(request("/api/sessions/delete", body)[0], 409)
            body["revision"] = preview["revision"]
            with patch("mirror_rebuild.backfill_relational", side_effect=sqlite3.OperationalError("injected")):
                self.assertEqual(request("/api/sessions/delete", body)[0], 500)
            self.assertEqual(self.store.history(), [old, keep])
            self.store.append("assistant", "added while preview open")
            self.assertEqual(request("/api/sessions/delete", body)[0], 409)
            _, preview = request(f"/api/sessions/deletion-preview?session_id={old['session_id']}")
            body["revision"] = preview["revision"]
            self.assertEqual(request("/api/sessions/delete", body)[0], 200)

    def test_http_recovery_failures_return_503_and_old_voice_turns_are_rejected(self):
        old, keep = self.pair()
        preview = self.plan(old)
        with self.server() as request:
            with patch("deletion._restore_mirror", side_effect=sqlite3.OperationalError("injected")):
                status, result = request("/api/sessions/delete", {
                    "session_id": old["session_id"], "confirmation": "DELETE", "revision": preview.revision})
                self.assertEqual(status, 503)
                self.assertIn("recovery", result["error"])
                self.assertEqual(request("/api/history")[0], 503)
            self.assertEqual(request("/api/history")[0], 200)
            self.assertEqual(request("/api/live-turn", {
                "user": "stale transcript", "call_id": str(uuid4()), "deletion_generation": None})[0], 409)
            self.assertEqual(self.store.history(), [keep])

    def test_background_records_include_all_direct_and_transitive_inputs(self):
        router = Mock(aliases=("test",), last_alias="test")
        router.chat_with_messages.return_value = RouterResult("SELF: A specific self observation.")
        events = [self.store.append("user", f"input {index}") for index in range(7)]
        heartbeat = Heartbeat(self.store, self.interior, router)
        heartbeat._reflection_pass()
        reflection = self.interior.list_reflections()[0]
        self.assertEqual(reflection["provenance"], provenance(events=events[-6:]))
        heartbeat._identity_proposal_pass()
        self.assertEqual(self.interior.list_identity()[0]["provenance"], reflection["provenance"])
        # An older reflection without a full source list cannot be laundered
        # into a new proposal that claims complete provenance.
        self.interior.append_reflection("old unknown", "self", source_event_id=events[0]["id"])
        heartbeat._identity_proposal_pass()
        self.assertFalse(self.interior.list_identity()[-1]["provenance"]["complete"])

    def test_digest_and_waiting_message_track_prior_reflection_sources(self):
        old, keep = self.pair()
        self.interior.append_reflection("earlier context", source_event_id=old["id"], provenance=provenance(events=[old]))
        router = Mock()
        router.chat_with_messages.return_value = RouterResult("A useful note.")
        proof = provenance(events=[keep])
        note = digest([{"gap": "question", "results": []}], router, self.interior, keep["id"], proof=proof)
        reach(note, router, self.interior, keep["id"], proof=proof)
        expected = provenance(events=[old, keep])
        self.assertEqual(self.interior.list_reflections()[-1]["provenance"], expected)
        self.assertEqual(self.interior.get_waiting_message()["provenance"], expected)
        self.assertTrue(any("another chat" in issue for issue in self.plan(old).unresolved))

    def test_another_process_appends_only_after_deletion_finishes(self):
        old, keep = self.pair()
        script = '''
import sys
from pathlib import Path
from events import EventStore
print("ready", flush=True)
EventStore(Path(sys.argv[1])).append("user", "other process accepted")
'''
        process = None
        try:
            with activity_gate(self.root, exclusive=True):
                process = subprocess.Popen([sys.executable, "-c", script, str(self.root)],
                    env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.assertEqual(process.stdout.readline().strip(), "ready")
                self.assertIsNone(process.poll())
                self.delete(self.plan(old))
                self.assertIsNone(process.poll())
            _, errors = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, errors)
            self.assertEqual([event["content"] for event in self.store.history()],
                             [keep["content"], "other process accepted"])
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()

    def test_legacy_ids_bytes_corrections_and_backfill_survive_two_deletions(self):
        rows = [
            {"role": "user", "content": "first", "said_at": "2020-01-01T00:00:00Z"},
            {"kind": "chat_boundary", "said_at": "2020-01-02T00:00:00Z"},
            {"role": "user", "content": "middle", "said_at": "2020-01-02T01:00:00Z"},
            {"kind": "chat_boundary", "said_at": "2020-01-03T00:00:00Z"},
            {"role": "user", "content": "last", "said_at": "2020-01-03T01:00:00Z", "source": "voice"},
        ]
        lines = [(json.dumps(row, separators=(",", ":")) + "\n").encode() for row in rows]
        self.store.path.write_bytes(b"".join(lines))
        self.store = EventStore(self.root, mirror=self.rel)
        original = self.store.history()
        correction = self.store.append_transcript_correction("legacy:4", "corrected last")
        correction_bytes = self.store.path.read_bytes().splitlines(keepends=True)[-1]
        self.delete(self.plan(original[0]))
        self.assertEqual(self.store.history()[0]["id"], "legacy:2")
        self.assertEqual(self.store.history()[0]["session_id"], legacy_session_id(1))
        self.delete(self.plan(original[1]))
        expected = [dict(original[2], content="corrected last", original_content="last")]
        self.assertEqual(self.store.history(), expected)
        self.assertEqual(EventStore(self.root).history(), expected)
        self.assertEqual(self.store.path.read_bytes(), lines[3] + lines[4] + correction_bytes)
        backfill_relational(self.rel, self.root)
        self.assertEqual(self.rel.fetchall("SELECT event_id,session_id FROM event_sessions"),
                         [{"event_id": "legacy:4", "session_id": legacy_session_id(3)}])
        self.assertEqual(self.rel.count("transcript_corrections"), 1)

    def test_stale_preview_and_reentrant_append_are_not_overwritten(self):
        old, keep = self.pair()
        plan = self.plan(old)
        self.store.append("assistant", "new accepted event")
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(DeletionError, "preview changed"):
            self.delete(plan)
        self.assertEqual(self.store.path.read_bytes(), before)
        with self.assertRaisesRegex(DeletionError, "preview changed"):
            self.delete(self.plan(old), before_commit=lambda: self.store.append("user", "concurrent saved"))
        self.assertEqual(self.store.history()[-1]["content"], "concurrent saved")
        self.assertIn(old["id"], [event["id"] for event in self.store.history()])
        self.assertFalse((self.root / deletion.TRANSACTION).exists())

    def test_plan_indexes_cannot_be_tampered_with(self):
        old, keep = self.pair()
        plan = self.plan(old)
        plan.paths["events"].add(2)
        self.delete(plan)
        self.assertEqual(self.store.history(), [keep])

    def test_acknowledged_waiting_and_identity_rulings_are_removed(self):
        old, keep = self.pair()
        proof = provenance(events=[old])
        reflection = self.interior.append_reflection("private note", provenance=proof, source_event_id=old["id"])
        proposal = self.interior.append_identity_proposal("private claim", [reflection["id"]], provenance=proof)
        self.interior.append_identity_ruling(proposal["id"], "ratified")
        self.interior.set_waiting_message("already read secret", source_event_id=old["id"], provenance=proof)
        self.interior.clear_waiting_message()
        plan = self.plan(old)
        self.assertEqual(plan.counts["waiting_message"], 1)
        self.assertEqual(plan.counts["identity"], 2)
        self.delete(plan)
        self.assertEqual(json.loads(self.interior.waiting_message_path.read_bytes()), {})
        self.assertEqual(self.interior.list_identity(), [])
        self.assertEqual(self.intr.count("identity_entries"), 0)
        self.assertEqual(self.intr.count("waiting_message"), 0)
        self.assertEqual(self.plan(keep).unresolved, [])

    def test_unknown_and_shared_provenance_block_without_changes(self):
        old, keep = self.pair()
        for proof in (None, provenance(records=[{}]), provenance(events=[old, keep])):
            with self.subTest(proof=proof):
                self.interior.append_reflection("derived", provenance=proof)
                before = self.store.path.read_bytes()
                with self.assertRaisesRegex(DeletionError, "blocked"):
                    self.delete(self.plan(old))
                self.assertEqual(before, self.store.path.read_bytes())

    def test_truncated_or_unknown_records_refuse_without_rewrite(self):
        old, keep = self.pair()
        base = self.store.path.read_bytes()
        for tail in (b'{"role":', b'null\n', b'{"kind":"future_type"}\n'):
            with self.subTest(tail=tail):
                self.store.path.write_bytes(base + tail)
                with self.assertRaises(DeletionError):
                    self.delete(self.plan(old))
                self.assertEqual(self.store.path.read_bytes(), base + tail)
        self.store.path.write_bytes(base)

    def test_precommit_disk_and_sqlite_failures_leave_originals(self):
        old, keep = self.pair()
        for target in ("deletion._write", "mirror_rebuild.backfill_relational"):
            with self.subTest(target=target):
                before = self.store.path.read_bytes()
                with patch(target, side_effect=sqlite3.OperationalError("injected")):
                    with self.assertRaises(sqlite3.Error):
                        self.delete(self.plan(old))
                self.assertEqual(self.store.path.read_bytes(), before)
                self.assertEqual(self.rel.count("events"), 2)
                self.assertFalse((self.root / deletion.TRANSACTION).exists())

    def test_committed_sqlite_failure_pauses_reads_then_recovers(self):
        old, keep = self.pair()
        with patch("deletion._restore_mirror", side_effect=sqlite3.OperationalError("injected")):
            with self.assertRaises(RecoveryRequired):
                self.delete(self.plan(old))
            with self.assertRaises(RecoveryRequired):
                self.store.history()
            with self.assertRaises(RecoveryRequired):
                self.rel.count("events")
        self.assertEqual(EventStore(self.root).history(), [keep])
        self.assertEqual(self.rel.fetchall("SELECT id FROM events"), [{"id": keep["id"]}])
        self.assertFalse((self.root / deletion.TRANSACTION).exists())

    def test_replace_failure_cannot_leave_mirrors_ahead_of_files(self):
        old, keep = self.pair()
        replace = os.replace
        def fail_events(source, target):
            if Path(target) == self.store.path:
                raise OSError("injected replace failure")
            return replace(source, target)
        with patch("deletion.os.replace", side_effect=fail_events):
            with self.assertRaises(RecoveryRequired):
                self.delete(self.plan(old))
            self.assertIn(b"DELETE_ME_UNIQUE", self.store.path.read_bytes())
            with self.assertRaises(RecoveryRequired):
                EventStore(self.root)
        self.assertEqual(EventStore(self.root).history(), [keep])
        self.assertEqual(self.rel.count("events"), 1)

    def test_stale_store_cannot_resume_deleted_session(self):
        old, keep = self.pair()
        self.store.open_session(old["session_id"])
        other = EventStore(self.root)
        self.delete(self.plan(old))
        self.assertNotEqual(other.current_session_id, old["session_id"])
        with self.assertRaises(KeyError):
            other.append("assistant", "stale reply", session_id=old["session_id"])
        new = other.append("user", "fresh")
        self.assertNotEqual(new["session_id"], old["session_id"])
        self.assertEqual(self.store.history()[0], keep)

    def test_other_session_deletion_preserves_resume_state_and_permissions(self):
        old, keep = self.pair()
        self.store.open_session(keep["session_id"])
        resumed = self.store._resumed_session_id
        self.store.path.chmod(0o600)
        self.delete(self.plan(old))
        self.assertEqual(self.store._resumed_session_id, resumed)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_inflight_operation_finishes_before_preview_is_revalidated(self):
        old, keep = self.pair()
        plan = self.plan(old)
        captured, release, attempted, finished = (threading.Event() for _ in range(4))
        errors = []
        def delayed_reply():
            with activity_gate(self.root):
                captured.set()
                self.assertTrue(release.wait(5))
                self.store.append("assistant", "delayed valid reply", session_id=old["session_id"])
        def remove():
            attempted.set()
            try:
                self.delete(plan)
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()
        writer = threading.Thread(target=delayed_reply)
        remover = threading.Thread(target=remove)
        writer.start()
        self.assertTrue(captured.wait(5))
        remover.start()
        self.assertTrue(attempted.wait(5))
        self.assertFalse(finished.wait(.05))
        release.set()
        writer.join(5)
        remover.join(5)
        self.assertIsInstance(errors[0], DeletionError)
        self.assertEqual(self.store.history()[-1]["content"], "delayed valid reply")
        self.delete(self.plan(old))
        self.assertEqual(self.store.history(), [keep])

    def test_process_crashes_at_each_commit_boundary_recover(self):
        script = '''
import os, sys
from pathlib import Path
import deletion
from events import EventStore
from interior import InteriorStore
root = Path(sys.argv[1]); step = sys.argv[2]
store = EventStore(root); interior = InteriorStore(root)
old = store.history()[0]
plan = deletion.build_deletion_plan(store, interior, old["session_id"])
replace = deletion.os.replace; mirror = deletion._restore_mirror
def crash_replace(source, target):
    replace(source, target)
    if str(Path(target).relative_to(root)) == step:
        os._exit(77)
def crash_mirror(source, target):
    mirror(source, target)
    if str(Path(target).relative_to(root)) == step:
        os._exit(77)
deletion.os.replace = crash_replace; deletion._restore_mirror = crash_mirror
hook = (lambda: os._exit(77)) if step == "prepared" else None
deletion.execute_deletion(store, interior, None, None, plan, "DELETE", before_commit=hook)
'''
        for step in ("prepared", ".session-deletion/COMMIT.json", *deletion.FILES.values(), *deletion.DATABASES):
            with self.subTest(step=step), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                store = EventStore(root)
                interior = InteriorStore(root)
                old = store.append("user", "gone")
                proof = provenance(events=[old])
                store.append_entity_observation("entity", "Entity", "gone", source_event_id=old["id"])
                store.append_heartbeat_completion("entities", old["id"])
                store.append_search_record("gone", [], "conversation", old["id"], provenance=proof)
                store.embeddings_path.write_text(json.dumps({"event_id": old["id"], "vector": [1., 0.]}) + "\n")
                reflection = interior.append_reflection("gone", provenance=proof)
                interior.append_identity_proposal("gone", [reflection["id"]], provenance=proof)
                interior.set_waiting_message("gone", provenance=proof)
                interior.metabolism_path.write_text(json.dumps({"id": str(uuid4()), "kind": "reach", "provenance": proof}) + "\n")
                store.append_chat_boundary()
                keep = store.append("user", "keep")
                result = subprocess.run([sys.executable, "-c", script, directory, step],
                    env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 77, result.stderr)
                history = EventStore(root).history()
                self.assertEqual(history, [old, keep] if step == "prepared" else [keep])
                self.assertFalse((root / deletion.TRANSACTION).exists())

    def test_fsync_failures_on_both_sides_of_commit_are_recoverable(self):
        for target in ("prepared", "committed"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                store, interior = EventStore(root), InteriorStore(root)
                old = store.append("user", "gone")
                store.append_chat_boundary()
                keep = store.append("user", "keep")
                plan = build_deletion_plan(store, interior, old["session_id"])
                original_fsync = deletion._fsync
                def fail_sync(path):
                    marker = root / deletion.TRANSACTION / "COMMIT.json"
                    if marker.exists() == (target == "committed"):
                        raise OSError("injected fsync failure")
                    original_fsync(path)
                with patch("deletion._fsync", side_effect=fail_sync):
                    with self.assertRaises(OSError):
                        execute_deletion(store, interior, None, None, plan, "DELETE")
                self.assertEqual(EventStore(root).history(), [old, keep] if target == "prepared" else [keep])
                self.assertFalse((root / deletion.TRANSACTION).exists())

    def test_symlinked_mirror_is_not_overwritten(self):
        old, keep = self.pair()
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory) / "unrelated.db"
            outside.write_bytes(b"must remain untouched")
            self.intr.close()
            self.intr.db_path.parent.mkdir(exist_ok=True)
            self.intr.db_path.symlink_to(outside)
            try:
                with self.assertRaisesRegex(DeletionError, "symlink"):
                    self.plan(old)
                self.assertEqual(outside.read_bytes(), b"must remain untouched")
            finally:
                self.intr.db_path.unlink()

    def test_embedding_rows_and_shared_call_group_keep_unrelated_event(self):
        old = self.store.append("user", "voice gone", source="voice", call_id="call", turn_id="one")
        self.store.append_chat_boundary()
        keep = self.store.append("assistant", "voice kept", source="voice", call_id="call", turn_id="two")
        vectors = [{"event_id": event["id"], "vector": [float(i), 1.]} for i, event in enumerate((old, keep))]
        self.store.embeddings_path.write_text("".join(json.dumps(row) + "\n" for row in vectors))
        for row in vectors:
            self.rel.store_embedding_vector(row["event_id"], row["vector"])
        self.delete(self.plan(old))
        self.assertEqual(self.rel.load_embedding_vectors(), {keep["id"]: [1., 1.]})
        self.assertEqual(json.loads(self.store.embeddings_path.read_bytes()), vectors[-1])
        self.assertEqual(self.rel.fetchall("SELECT event_id,call_id,turn_id FROM voice_event_context"),
                         [{"event_id": keep["id"], "call_id": "call", "turn_id": "two"}])
        with self.server() as request:
            status, result = request("/api/calls")
            self.assertEqual(status, 200)
            self.assertNotIn("voice gone", json.dumps(result))
            self.assertIn("voice kept", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
