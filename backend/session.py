from config.settings import HISTORY_TURNS


class ConversationSession:
    """Tiny in-memory conversation session."""

    def __init__(self):
        self._turns: list[dict] = []

    def append(self, role: str, content: str):
        """Append a turn to the conversation."""
        self._turns.append({"role": role, "content": content})

    def history(self) -> list[dict]:
        """Return the last HISTORY_TURNS*2 messages for context."""
        # Each turn is one user+assistant pair, so 2 messages per turn
        max_messages = HISTORY_TURNS * 2
        return self._turns[-max_messages:] if self._turns else []


# One process-global session
session = ConversationSession()