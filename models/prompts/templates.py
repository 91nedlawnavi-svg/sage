from datetime import datetime
from config.settings import HISTORY_TURNS


def build_chat_messages(directive: str, user_input: str, history: list[dict]) -> list[dict]:
    """Build messages for chat completion with directive-first system prompt."""
    # Time context
    time_context = f"[Current date and time: {datetime.now():%A, %B %d, %Y at %I:%M %p}]"

    # System content: directive ALWAYS first, then time context
    # Extension points clearly marked for future phases
    system_content = (
        directive.strip()
        + "\n\n"
        + time_context
        + "\n\n"
        + "[ELLIOT'S MEMORY — extension point]\n"
        + "[SAGE'S INNER CONTEXT — extension point]\n"
        + "[SEARCH BLOCKS — extension point]"
    )

    # Build messages: system + history + user
    messages = [{"role": "system", "content": system_content}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_input})

    return messages