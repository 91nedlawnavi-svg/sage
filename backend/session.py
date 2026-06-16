import time
from config.settings import HISTORY_TURNS

# Upper bound for idle_seconds. Kept finite so reflection prompts never get
# absurd values, but high enough to tell "a short break" apart from "alone all
# night". The old 1h cap flattened every long absence to the same number and
# erased any sense of elapsed time.
MAX_IDLE_CAP = 172800  # 48 hours


class ConversationSession:
    """Tiny in-memory conversation session."""

    def __init__(self):
        self._turns: list[dict] = []
        # Track last user activity for heartbeat idle detection
        self._last_user_activity_ts: float = time.time()
        # Track in-flight chat requests so heartbeat knows not to run
        self._active_chats: int = 0

    def append(self, role: str, content: str):
        """Append a turn to the conversation."""
        self._turns.append({"role": role, "content": content})

    def begin_chat(self):
        """Record the start of an active chat request.

        Immediately marks user activity so heartbeat sees recent activity,
        then increments the active-chat counter so heartbeat knows to skip
        private reflection while Sage is replying.
        """
        self.touch()
        self._active_chats += 1

    def end_chat(self):
        """Record the end of an active chat request.

        Decrements the active-chat counter, never below zero, so a chain of
        end-calls from unclean shutdowns cannot corrupt the state.
        """
        if self._active_chats > 0:
            self._active_chats -= 1

    def chat_active(self) -> bool:
        """Return True while a chat request is in flight."""
        return self._active_chats > 0

    def history(self) -> list[dict]:
        """Return the last HISTORY_TURNS*2 messages for context."""
        # Each turn is one user+assistant pair, so 2 messages per turn
        max_messages = HISTORY_TURNS * 2
        return self._turns[-max_messages:] if self._turns else []

    def touch(self):
        """Mark the last user activity as now."""
        self._last_user_activity_ts = time.time()

    def idle_seconds(self) -> float:
        """Return seconds since last user message, capped at MAX_IDLE_CAP."""
        elapsed = time.time() - self._last_user_activity_ts
        return min(elapsed, MAX_IDLE_CAP)

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