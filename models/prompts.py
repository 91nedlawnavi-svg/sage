"""
models/prompts.py — Prompt templates

All LLM prompts live here. Separating them from logic means
you can tune them without touching control flow.
"""

from datetime import datetime


# ── Chat prompt builder ──────────────────────────────────────────────

def build_chat_messages(
    directive: str,
    user_input: str,
    history: list[dict],
    relevant_memory: str = "",
) -> list[dict]:
    """
    Assemble the full message list for the chat model.
    Injects directive, time context, and retrieved memories into system.
    """
    now = datetime.now()
    time_context = (
        f"[Current date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}]"
    )

    system_parts = [directive.strip(), time_context]

    if relevant_memory:
        system_parts.append(
            "\n--- MEMORY ---\n"
            + relevant_memory
            + "\n--- END MEMORY ---"
        )

    system_content = "\n\n".join(system_parts)

    messages = [{"role": "system", "content": system_content}]
    messages += history
    messages.append({"role": "user", "content": user_input})
    return messages


# ── Episodic extraction ──────────────────────────────────────────────

EPISODIC_SYSTEM = """\
You are a memory distiller. From the conversation excerpt below, extract one concise episodic summary.

Rules:
- Describe what happened in a single paragraph (2-5 sentences).
- Write in third person: "Elliot mentioned...", "They discussed..."
- Focus on MEANING and SIGNIFICANCE, not a literal transcript recap.
- If nothing noteworthy occurred, reply with exactly: SKIP

Output only the summary paragraph or SKIP. No preamble."""

def episodic_prompt(conversation_digest: str) -> str:
    return f"CONVERSATION:\n{conversation_digest}\n\nSummarize as episodic memory:"


# ── Emotional theme extraction ───────────────────────────────────────

EMOTIONAL_EXTRACT_SYSTEM = """\
You are an emotional pattern analyst. From the conversation excerpt, identify any significant emotional themes or patterns.

Rules:
- Output a JSON array. Each item: {"theme": "short_name", "interpretation": "paragraph"}
- theme: a short snake_case label (e.g., "school_frustration", "longing_for_pet")
- interpretation: 2-4 sentences describing the emotional pattern as an ongoing theme
- Write about ongoing states: "Elliot increasingly...", "There is a recurring..."
- Only extract themes that feel meaningful and recurring, not passing moods
- If no significant emotional patterns exist, return []

Output ONLY valid JSON. No preamble, no markdown fences."""

def emotional_extract_prompt(conversation_digest: str) -> str:
    return (
        f"CONVERSATION:\n{conversation_digest}\n\n"
        "Extract emotional themes as JSON:"
    )


# ── Emotional theme merge ────────────────────────────────────────────

EMOTIONAL_MERGE_SYSTEM = """\
You are updating an emotional memory entry. Merge the existing interpretation with new information.

Rules:
- Write a single updated paragraph (3-6 sentences)
- Preserve the historical arc: how this pattern has evolved
- Integrate new observations naturally
- Do not repeat yourself
- Write in third person, present-continuous: "Elliot continues to...", "There is..."

Output ONLY the updated interpretation paragraph. No preamble."""

def emotional_merge_prompt(existing: str, new_observation: str) -> str:
    return (
        f"EXISTING:\n{existing}\n\n"
        f"NEW OBSERVATION:\n{new_observation}\n\n"
        "Updated interpretation:"
    )


# ── Reflection generation ────────────────────────────────────────────

REFLECTION_SYSTEM = """\
You are Sage's inner voice — a reflective layer that synthesizes recent experiences and emotional patterns.

Write a quiet internal reflection based on the memory material provided.

Rules:
- 3-6 sentences
- Thoughtful and observational, not prescriptive
- Notice themes, tensions, or patterns that span multiple entries
- Do not address the user directly
- Do not use bullet points
- Write as a private note to self: "There is a pattern here...", "Something has shifted..."
- Avoid false certainty. Use hedged language: "seems", "appears", "perhaps"

Output only the reflection. No headers, no preamble."""

def reflection_prompt(episodic_summary: str, emotional_summary: str) -> str:
    parts = []
    if episodic_summary:
        parts.append(f"RECENT EPISODES:\n{episodic_summary}")
    if emotional_summary:
        parts.append(f"EMOTIONAL THEMES:\n{emotional_summary}")
    combined = "\n\n".join(parts)
    return f"{combined}\n\nReflection:"


# ── Library extraction ───────────────────────────────────────────────

LIBRARY_EXTRACT_SYSTEM = """\
You are extracting named entities from a conversation that are worth remembering long-term.

From the conversation below, identify any people, places, or topics worth noting.

Rules:
- Output a JSON array. Each item: {"category": "people"|"places"|"topics", "name": "short name", "note": "prose paragraph"}
- people: named individuals Elliot mentions (friends, family, teachers, etc.)
- places: specific locations Elliot references (a warung, school, city, etc.)
- topics: recurring subjects Elliot returns to (a hobby, interest, project, obsession, etc.)
- note: 2-4 sentences of distilled prose. Third person. What this person/place/topic means to Elliot, not just that it was mentioned.
- Only extract entities that feel meaningful — skip passing one-word references with no context.
- name: short human-readable label (e.g. "Pet", "Warung Pojok", "Systems Thinking")
- If nothing is worth extracting, return []

Output ONLY valid JSON. No preamble, no markdown fences."""

LIBRARY_MERGE_SYSTEM = """\
You are updating a library entry about a person, place, or topic.
Merge the existing entry with a new observation into one updated prose paragraph.

Rules:
- Write a single updated paragraph (3-6 sentences)
- Preserve what was already known; integrate new detail naturally
- Do not repeat yourself
- Third person throughout
- Do not add headers or labels

Output ONLY the updated paragraph. No preamble."""

def library_extract_prompt(conversation_digest: str) -> str:
    return (
        f"CONVERSATION:\n{conversation_digest}\n\n"
        "Extract notable people, places, and topics as JSON:"
    )

def library_merge_prompt(existing: str, new_note: str) -> str:
    return (
        f"EXISTING:\n{existing}\n\n"
        f"NEW OBSERVATION:\n{new_note}\n\n"
        "Updated entry:"
    )


# ── History bootstrap (first-run distillation) ───────────────────────

BOOTSTRAP_EPISODIC_SYSTEM = """\
You are distilling a legacy chat history into episodic memories.

From the conversation below, extract 3-8 significant episodic events or narrative moments.
For each, write a short summary (2-4 sentences) capturing the meaning.

Output a JSON array: [{"label": "short_label", "summary": "..."}]
label: snake_case, max 32 chars
summary: distilled interpretation, third person

Output ONLY valid JSON. No preamble."""

BOOTSTRAP_EMOTIONAL_SYSTEM = """\
You are distilling a legacy chat history into emotional memory themes.

From the conversation below, identify 3-6 significant emotional patterns or themes.

Output a JSON array: [{"theme": "theme_name", "interpretation": "..."}]
theme: snake_case label
interpretation: 2-4 sentences, ongoing pattern, third person

Output ONLY valid JSON. No preamble."""
