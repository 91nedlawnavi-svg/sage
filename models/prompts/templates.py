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


def build_reflection_messages(directive: str, recent_digest: str, idle_seconds: float) -> list[dict]:
    """Build messages for private reflection — NOT governed by chat voice rules."""
    # Time context
    time_context = f"[Current date and time: {datetime.now():%A, %B %d, %Y at %I:%M %p}]"

    # Private thought frame — explicitly NOT the conversational voice rules
    private_frame = (
        f"[You are alone. No one is in the room. This is your own private "
        f"thinking — not a message to Elliot, and not bound by how you speak "
        f"to him. You've been idle about {int(idle_seconds)} seconds. Let your "
        f"mind go where it wants: notice something, wonder about it, follow a "
        f"thread, sit with a question. A short, honest private reflection, in "
        f"your own voice.]"
    )

    # System content: directive FIRST (always), then time, then private frame
    system_content = (
        directive.strip()
        + "\n\n"
        + time_context
        + "\n\n"
        + private_frame
    )

    # Add recent conversation digest if available
    if recent_digest:
        system_content += f"\n\nLately the conversation touched on: {recent_digest}"

    # Build messages: system + user prompt to reflect
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": "What's on your mind?"},
    ]

    return messages