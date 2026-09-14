from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from database import interior_db
from interior import InteriorStore


class WaitingMessageDurabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.mirror = interior_db(self.root)
        self.store = InteriorStore(self.root, mirror=self.mirror)

    def tearDown(self) -> None:
        self.mirror.close()
        self.temporary_directory.cleanup()

    def assert_original_survives_restart(self, original: dict, original_bytes: bytes) -> None:
        self.assertEqual(self.store.waiting_message_path.read_bytes(), original_bytes)
        self.assertEqual(InteriorStore(self.root).get_waiting_message(), original)
        self.assertEqual(list(self.store.interior_dir.glob(".waiting_message.json.*.tmp")), [])
        mirror = self.mirror.fetchone("SELECT content, read FROM waiting_message WHERE id = 1")
        self.assertEqual(mirror, {"content": original["content"], "read": 0})

    def test_failed_waiting_revision_preserves_previous_message(self) -> None:
        original = self.store.set_waiting_message("Already saved waiting message")
        original_bytes = self.store.waiting_message_path.read_bytes()

        def interrupted_write(value, stream, **kwargs):
            stream.write('{"content":')
            raise OSError("injected disk full during waiting revision")

        with patch("interior.json.dump", side_effect=interrupted_write):
            with self.assertRaisesRegex(OSError, "injected disk full"):
                self.store.set_waiting_message("New waiting revision")

        self.assert_original_survives_restart(original, original_bytes)

    def test_failed_waiting_acknowledgement_preserves_existing_record(self) -> None:
        original = self.store.set_waiting_message("Already saved waiting message")
        original_bytes = self.store.waiting_message_path.read_bytes()

        def interrupted_write(value, stream, **kwargs):
            stream.write('{"content":')
            raise OSError("injected disk full during acknowledgement")

        with patch("interior.json.dump", side_effect=interrupted_write):
            self.store.clear_waiting_message()

        self.assert_original_survives_restart(original, original_bytes)

    def test_failed_replacement_preserves_previous_message(self) -> None:
        for operation in ("revision", "acknowledgement"):
            with self.subTest(operation=operation):
                original = self.store.set_waiting_message("Already saved waiting message")
                original_bytes = self.store.waiting_message_path.read_bytes()
                with patch("interior.os.replace", side_effect=OSError("injected replace failure")):
                    if operation == "revision":
                        with self.assertRaisesRegex(OSError, "injected replace failure"):
                            self.store.set_waiting_message("New waiting revision")
                    else:
                        self.store.clear_waiting_message()
                self.assert_original_survives_restart(original, original_bytes)

    def test_successful_revision_and_acknowledgement_remain_single_record(self) -> None:
        original = self.store.set_waiting_message("First waiting message")
        revised = self.store.set_waiting_message("Revised waiting message")

        self.assertEqual(revised["said_at"], original["said_at"])
        self.assertIn("revised_at", revised)
        self.assertEqual(InteriorStore(self.root).get_waiting_message(), revised)
        self.assertEqual(len(list(self.store.interior_dir.glob("waiting_message.json"))), 1)

        self.store.clear_waiting_message()
        saved = json.loads(self.store.waiting_message_path.read_text(encoding="utf-8"))
        self.assertTrue(saved["read"])
        self.assertIsNone(InteriorStore(self.root).get_waiting_message())


if __name__ == "__main__":
    unittest.main()
