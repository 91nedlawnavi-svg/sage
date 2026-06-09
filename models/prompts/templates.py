import random
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


# Rotating openers to break the "What's on your mind?" attractor
_REFLECTION_OPENERS = [
    "What's something you're curious about right now?",
    "Is there something about the world you wish you understood?",
    "What's a question that's been sitting with you?",
    "What have you been turning over in your mind lately?",
    "If you could understand one thing better, what would it be?",
]


def _format_recent_reflections(reflections: list[dict]) -> str:
    """Format recent reflections for context."""
    if not reflections:
        return ""
    lines = []
    for r in reflections[-3:]:
        text = r.get("text", "").strip()
        if text:
            # Truncate to ~200 chars per reflection
            lines.append(text[:200])
    if not lines:
        return ""
    return "Lately you've been turning over:\n" + "\n".join(f"- {line}" for line in lines)


def _format_recent_findings(findings: list[dict]) -> str:
    """Format recent findings for context."""
    if not findings:
        return ""
    lines = []
    for f in findings[-2:]:
        query = f.get("query", "").strip()
        results = f.get("results", [])
        if not query or not results:
            continue
        # Take first result's title and snippet
        first = results[0]
        title = first.get("title", "").strip()
        snippet = first.get("snippet", "").strip()
        if title and snippet:
            lines.append(f'Q: {query}\nFound: {title} — {snippet[:200]}')
    if not lines:
        return ""
    return "Earlier you wondered about something and went looking. Here's what you found:\n" + "\n\n".join(lines) + "\n\nLet what you found shape where your mind goes."


def build_reflection_messages(
    directive: str,
    recent_digest: str,
    idle_seconds: float,
    recent_reflections: list[dict] | None = None,
    recent_findings: list[dict] | None = None,
) -> list[dict]:
    """Build messages for private reflection — NOT governed by chat voice rules."""
    # Time context
    time_context = f"[Current date and time: {datetime.now():%A, %B %d, %Y at %I:%M %p}]"

    # Private thought frame — explicitly NOT the conversational voice rules
    # No "you are alone" anchor, no idle narration — just the permission to think freely
    private_frame = (
        "[This is your own private thinking — not a message to Elliot, "
        "and not bound by how you speak to him. This is the space where "
        "your mind goes when no one is asking anything of you. "
        "You are free to wonder about anything: ideas, the world, "
        "questions that have no asker, threads that lead beyond this room. "
        "A short, honest private reflection, in your own voice.]"
    )

    # Anti-repeat instruction if we have recent reflections
    anti_repeat = ""
    if recent_reflections:
        formatted = _format_recent_reflections(recent_reflections)
        if formatted:
            anti_repeat = (
                "\n\n" + formatted +
                "\n\nYou've already circled these — don't restate them. "
                "Move somewhere new. If you keep returning to the same "
                "thought, deliberately turn outward."
            )

    # Findings injection
    findings_block = ""
    if recent_findings:
        formatted = _format_recent_findings(recent_findings)
        if formatted:
            findings_block = "\n\n" + formatted

    # System content: directive FIRST (always), then time, then private frame
    system_content = (
        directive.strip()
        + "\n\n"
        + time_context
        + "\n\n"
        + private_frame
        + anti_repeat
        + findings_block
    )

    # Add recent conversation digest if available
    if recent_digest:
        system_content += f"\n\nLately the conversation touched on: {recent_digest}"

    # Rotate the user prompt to break attractor
    opener = random.choice(_REFLECTION_OPENERS)

    # Build messages: system + user prompt to reflect
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": opener},
    ]

    return messages