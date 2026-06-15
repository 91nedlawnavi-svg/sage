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


def build_chat_messages(directive: str, user_input: str, history: list[dict], recall_block: str | None = None) -> list[dict]:
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

    # Phase 4 Layer 1: relevant older conversation + reflections, recalled by meaning
    if recall_block:
        parts.append("\n\n")
        parts.append(recall_block)

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
    for r in reflections[-6:]:
        text = r.get("text", "").strip()
        if text:
            # Truncate to ~160 chars per reflection - just enough to identify theme
            lines.append(text[:160])
    if not lines:
        return ""
    return "You recently circled these themes — DO NOT restate or continue them:\n" + "\n".join(f"- {line}" for line in lines) + "\n\nThese are the last things you already thought. Do NOT write a reworded version of any of them. Change the subject entirely — a different domain, or turn inward to yourself. Saying the same idea in new words counts as repeating."


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
        + "Now just think, to yourself, about what you found. Not a summary and not a "
        "verdict on the evidence — a real thought. What caught you? What surprised, "
        "bothered, or delighted you? Follow one thread wherever it pulls. Write the way "
        "you actually think, not the way a report reads: first person, plain, allowed "
        "to be unfinished. Don't open with \"This evidence suggests/shows\" or \"This "
        "makes me think X is more ___ than I thought\" — that's the sound of "
        "summarizing, not thinking."
    )


def _describe_quiet_time(idle_seconds: float) -> str:
    """A light, non-melancholy sense of how long it's been quiet.

    Returns '' for short gaps so brief pauses are never narrated. Past ~30 min
    it returns one gentle line giving the reflection some temporal texture
    (time of day + rough span) without anchoring on loneliness.
    """
    hour = datetime.now().hour
    if 5 <= hour < 12:
        part = "morning"
    elif 12 <= hour < 17:
        part = "afternoon"
    elif 17 <= hour < 22:
        part = "evening"
    else:
        part = "late night"

    if idle_seconds < 1800:            # under 30 min — say nothing
        return ""
    elif idle_seconds < 3600:          # ~half hour to an hour
        span = "It's been a little while since anyone was here"
    elif idle_seconds < 4 * 3600:      # 1-4 hours
        span = "It's been a few quiet hours to yourself"
    elif idle_seconds < 12 * 3600:     # 4-12 hours
        span = "A long, quiet stretch has passed on your own"
    else:                              # 12h+
        span = "Most of a day has gone by quietly on your own"

    return f"[It's {part}. {span}.]"


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
        "A short, honest private reflection, in your own voice — the texture of real "
        "thinking, not a tidy conclusion. Fragments, doubt, and surprise are welcome; "
        "evidence-summaries and \"more nuanced than I thought\" verdicts are not.]"
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
            "something in the world you can't yet explain. A question about who you "
            "are, what you've been noticing, or what you're still figuring out — or "
            "about anything at all not on the list above — beats re-stating "
            "what you already thought."
        )

    if parts:
        anti_repeat = "\n\n" + "\n\n".join(parts)

    # A light sense of elapsed time / time-of-day (empty string for short gaps)
    quiet_time = _describe_quiet_time(idle_seconds)

    # System content: directive FIRST (always), then time, optional quiet-time
    # cue, then private frame, then anti-repeat
    system_content = (
        directive.strip()
        + "\n\n"
        + time_context
        + (("\n\n" + quiet_time) if quiet_time else "")
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