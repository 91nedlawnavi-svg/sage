import time
from config.settings import HISTORY_TURNS


class ConversationSession:
    """Tiny in-memory conversation session."""

    def __init__(self):
        self._turns: list[dict] = []
        # Track last user activity for heartbeat idle detection
        self._last_user_activity_ts: float = time.time()

    def append(self, role: str, content: str):
        """Append a turn to the conversation."""
        self._turns.append({"role": role, "content": content})

    def history(self) -> list[dict]:
        """Return the last HISTORY_TURNS*2 messages for context."""
        # Each turn is one user+assistant pair, so 2 messages per turn
        max_messages = HISTORY_TURNS * 2
        return self._turns[-max_messages:] if self._turns else []

    def touch(self):
        """Mark the last user activity as now."""
        self._last_user_activity_ts = time.time()

    def idle_seconds(self) -> float:
        """Return seconds since last user message."""
        return time.time() - self._last_user_activity_ts

    def recent_digest(self) -> str:
        """Return a short digest of recent conversation topics."""
        if not self._turns:
            return ""
        # Use last few user messages to infer topics
        user_msgs = [t["content"] for t in self._turns if t["role"] == "user"]
        if not user_msgs:
            return ""
        # Take last 3 user messages, truncate each
        recent = user_msgs[-3:]
        return "; ".join(m[:120] for m in recent)


# One process-global session
session = ConversationSession()