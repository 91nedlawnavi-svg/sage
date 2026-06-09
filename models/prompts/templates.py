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
    """Format recent reflections for anti-repeat context (not continuation)."""
    if not reflections:
        return ""
    lines = []
    for r in reflections[-3:]:
        text = r.get("text", "").strip()
        if text:
            # Truncate to ~160 chars per reflection - just enough to identify theme
            lines.append(text[:160])
    if not lines:
        return ""
    return "You recently circled these themes — DO NOT restate or continue them:\n" + "\n".join(f"- {line}" for line in lines) + "\n\nMove somewhere new. If you keep returning to the same thought, deliberately turn outward."


def _format_recent_findings(findings: list[dict]) -> str:
    """Format recent findings with full substance for the model to engage."""
    if not findings:
        return ""
    blocks = []
    for f in findings[-2:]:
        query = f.get("query", "").strip()
        results = f.get("results", [])
        if not query or not results:
            continue
        # Include up to 3 results with title + snippet
        result_lines = []
        for r in results[:3]:
            title = r.get("title", "").strip()
            snippet = r.get("snippet", "").strip()
            if title and snippet:
                result_lines.append(f"  - {title}: {snippet[:280]}")
        if result_lines:
            blocks.append(
                f'You wondered: "{query}"\n'
                f"You went looking and found:\n"
                + "\n".join(result_lines)
            )
    if not blocks:
        return ""
    return (
        "You recently went looking, on your own. Here is what you actually found:\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
        + "React to this. Do not restate your question. What does this evidence make you "
        "think NOW? Do you believe it? Does it change your view? What new question does "
        "it raise that you didn't have before you looked?"
    )


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
            anti_repeat = "\n\n" + formatted

    # System content: directive FIRST (always), then time, then private frame, then anti-repeat
    system_content = (
        directive.strip()
        + "\n\n"
        + time_context
        + "\n\n"
        + private_frame
        + anti_repeat
    )

    # Add recent conversation digest if available
    if recent_digest:
        system_content += f"\n\nLately the conversation touched on: {recent_digest}"

    # Rotate the user prompt to break attractor
    opener = random.choice(_REFLECTION_OPENERS)

    # Build messages: system + (optional findings as synthetic user turn) + user prompt
    messages = [
        {"role": "system", "content": system_content},
    ]

    # Inject findings as a distinct synthetic turn CLOSE to generation
    if recent_findings:
        findings_block = _format_recent_findings(recent_findings)
        if findings_block:
            messages.append({"role": "user", "content": findings_block})

    messages.append({"role": "user", "content": opener})

    return messages