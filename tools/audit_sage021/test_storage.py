"""SAGE-021 storage probes. Temporary roots only; expected behavior assertions."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from database import relational_db
from events import EventStore
from interior import InteriorStore
from mirror_rebuild import backfill_relational, verify
from persistence import append_jsonl, read_jsonl


class StorageAuditTests(unittest.TestCase):
    def test_jsonl_every_utf8_crash_offset_keeps_complete_records(self):
        with tempfile.TemporaryDirectory(prefix="sage021-storage-") as directory:
            path = Path(directory) / "records.jsonl"
            first = {"content": "First complete record"}
            second = {"content": "Café 漢字 🌱", "source": "user"}
            appended = {"content": "Next accepted record"}
            prefix = (json.dumps(first, ensure_ascii=False) + "\n").encode()
            tail = (json.dumps(second, ensure_ascii=False) + "\n").encode()
            for offset in range(len(tail) + 1):
                with self.subTest(offset=offset):
                    path.write_bytes(prefix + tail[:offset])
                    complete_before = read_jsonl(path)
                    append_jsonl(path, [appended])
                    self.assertEqual(read_jsonl(path), complete_before + [appended])
                    self.assertTrue(path.read_bytes().startswith(prefix))

    def test_four_processes_preserve_every_concurrent_append(self):
        with tempfile.TemporaryDirectory(prefix="sage021-storage-") as directory:
            root = Path(directory)
            EventStore(root).append("user", "seed", save_embedding=False)
            script = (
                "from pathlib import Path\n"
                "import sys\n"
                "from events import EventStore\n"
                "store = EventStore(Path(sys.argv[1]))\n"
                "for index in range(30):\n"
                "    store.append('user', f'{sys.argv[2]}:{index}', save_embedding=False)\n"
            )
            environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
            processes = []
            try:
                for index in range(4):
                    processes.append(subprocess.Popen(
                        [sys.executable, "-c", script, str(root), str(index)],
                        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    ))
                for process in processes:
                    _, errors = process.communicate(timeout=15)
                    self.assertEqual(process.returncode, 0, errors)
                history = EventStore(root).history()
                expected = {"seed"} | {f"{worker}:{index}" for worker in range(4) for index in range(30)}
                self.assertEqual(len(history), len(expected))
                self.assertEqual({event["content"] for event in history}, expected)
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()

    def test_backfill_legacy_entity_without_source_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="sage021-storage-") as directory:
            root = Path(directory)
            database = relational_db(root)
            try:
                store = EventStore(root)
                store.append_entity_observation("person", "Person", "Legacy observation")
                first = backfill_relational(database, root)
                second = backfill_relational(database, root)
                self.assertEqual(first["entity_observations"], 1)
                self.assertEqual(second["entity_observations"], 1)
                self.assertEqual(verify(second, {}, root), [])
            finally:
                database.close()

    def test_failed_waiting_revision_preserves_previous_message(self):
        with tempfile.TemporaryDirectory(prefix="sage021-storage-") as directory:
            store = InteriorStore(Path(directory))
            original = store.set_waiting_message("Already saved waiting message")

            def interrupted_write(value, stream, **kwargs):
                stream.write('{"content":')
                raise OSError("injected disk full during waiting revision")

            with patch("interior.json.dump", side_effect=interrupted_write):
                with self.assertRaisesRegex(OSError, "injected disk full"):
                    store.set_waiting_message("New waiting revision")
            self.assertEqual(store.get_waiting_message(), original)

    def test_failed_waiting_acknowledgement_preserves_existing_record(self):
        with tempfile.TemporaryDirectory(prefix="sage021-storage-") as directory:
            store = InteriorStore(Path(directory))
            original = store.set_waiting_message("Already saved waiting message")

            def interrupted_write(value, stream, **kwargs):
                stream.write('{"content":')
                raise OSError("injected disk full during acknowledgement")

            with patch("interior.json.dump", side_effect=interrupted_write):
                store.clear_waiting_message()
            self.assertTrue(store.waiting_message_path.exists(), "Acknowledgement error deleted original record")
            self.assertEqual(json.loads(store.waiting_message_path.read_text()), original)


if __name__ == "__main__":
    unittest.main()
