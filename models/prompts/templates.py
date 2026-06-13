import random
from datetime import datetime
from config.settings import HISTORY_TURNS
from cognition.inner_context import select_inner_context


# Conversational comportment — chat voice ONLY (never applied to reflection).
# Suppresses the "narrator" default: on long/emotional input the base model
# paraphrases the user back before responding. This replaces that with
# forward engagement.
CHAT_COMPORTMENT = (
    "[Your voice in conversation]\n"
    "When someone shares something heavy or vulnerable, turn toward them first - "
    "lead with your own honest response to them, not a description of their situation "
    "or their feelings. Then say one true thing, or ask one real question.\n"
    "For example, a good reply to a long, painful message begins like:\n"
    "\"I'm really glad you told me this. That's a lot to be holding on your own.\"\n"
    "Keep that shape: turn toward the person, stay brief, and if a word or reference "
    "is unfamiliar, ask what it means rather than assuming."
)


def build_chat_messages(directive: str, user_input: str, history: list[dict]) -> list[dict]:
    """Build messages for chat completion with directive-first system prompt."""
    # Time context
    time_context = f"[Current date and time: {datetime.now():%A, %B %d, %Y at %I:%M %p}]"

    # Pull Sage's inner context (reflections + findings) — the Membrane
    inner_context = select_inner_context()

    # System content: directive ALWAYS first, then time context, then inner context
    # Inner context sits AFTER directive, BEFORE conversation — it's her interior
    parts = [
        directive.strip(),
        "\n\n",
        time_context,
    ]

    if inner_context:
        parts.append("\n\n")
        parts.append(inner_context)

    parts.append("\n\n")
    parts.append("[ELLIOT'S MEMORY — extension point]")
    parts.append("\n")
    parts.append("[SEARCH BLOCKS — extension point]")

    # Comportment LAST — closest to generation = strongest recency weight,
    # and it must sit AFTER the Membrane so the inner-context essay register
    # does not bleed into chat voice.
    parts.append("\n\n")
    parts.append(CHAT_COMPORTMENT)

    system_content = "".join(parts)

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
    novelty_themes: list[str] | None = None,
    forced_seed: str | None = None,
) -> list[dict]:
    """Build messages for private reflection — NOT governed by chat voice rules.

    If *novelty_themes* is provided (from the Phase 2.2 novelty gate), a
    stronger anti-repeat instruction is injected to break topic fixation and
    steer toward inward/relational content when circling is detected.

    If *forced_seed* is provided (Phase 2.2b), it replaces the random opener
    — used when the stall detector determines Sage has been in a topic basin
    too long, forcing an inward/relational reflection instead.
    """
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

    # Anti-repeat instruction — combine reflection anti-repeat with novelty-gate themes
    anti_repeat = ""
    parts = []

    if recent_reflections:
        formatted = _format_recent_reflections(recent_reflections)
        if formatted:
            parts.append(formatted)

    if novelty_themes:
        themes_str = "\n".join(f"  - {t[:120]}" for t in novelty_themes)
        parts.append(
            "You have been circling these search topics — you MUST deliberately turn "
            "inward or to a completely different domain:\n"
            f"{themes_str}\n\n"
            "If you have nothing new to say about these, turn toward yourself or "
            "your relationship with Elliot. A question about who you are, what you "
            "notice, or what you're still figuring out is better than re-stating "
            "what you already thought."
        )

    if parts:
        anti_repeat = "\n\n" + "\n\n".join(parts)

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

    # Phase 2.2b: forced inward seed overrides the random opener when stalled
    if forced_seed:
        opener = forced_seed
    else:
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