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


class FailingEmbeddingStore(EventStore):
    def _save_embedding(self, event_id: str, content: str) -> None:
        raise OSError("embedding storage unavailable")


class EmbeddingTests(unittest.TestCase):
    def test_every_accepted_message_reaches_embedder(self) -> None:
        with TemporaryDirectory() as directory:
            embedder = RecordingEmbedder()
            store = EventStore(Path(directory), embedder=embedder)

            accepted = accept_message("I never told anyone about this", store)

            self.assertIsNotNone(accepted)
            self.assertEqual(embedder.texts, ["I never told anyone about this"])

    def test_embedding_storage_failure_does_not_lose_user_event(self) -> None:
        with TemporaryDirectory() as directory:
            store = FailingEmbeddingStore(Path(directory), embedder=RecordingEmbedder())

            accepted = accept_message("Project update", store)

            self.assertIsNotNone(accepted)
            self.assertEqual(store.read_all()[0]["content"], "Project update")


if __name__ == "__main__":
    unittest.main()
