from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from events import EventStore
from sage import accept_message


class RecordingEmbedder:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        return [1.0]


class PrivacyEmbeddingTests(unittest.TestCase):
    def test_held_close_input_never_reaches_embedder(self) -> None:
        with TemporaryDirectory() as directory:
            embedder = RecordingEmbedder()
            store = EventStore(Path(directory), embedder=embedder)

            accepted = accept_message("I never told anyone about this", store)

            self.assertIsNotNone(accepted)
            self.assertTrue(accepted.privacy.held_close)
            self.assertEqual(embedder.texts, [])

    def test_non_held_input_still_reaches_embedder(self) -> None:
        with TemporaryDirectory() as directory:
            embedder = RecordingEmbedder()
            store = EventStore(Path(directory), embedder=embedder)

            accepted = accept_message("Open project update", store)

            self.assertIsNotNone(accepted)
            self.assertFalse(accepted.privacy.held_close)
            self.assertEqual(embedder.texts, ["Open project update"])


if __name__ == "__main__":
    unittest.main()
