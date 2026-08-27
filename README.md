# Sage

Sage is an owned, persistent personal intelligence for Elliot.

The long-term goal is JARVIS-like: a daily-life companion and general-purpose
assistant that remembers the whole history, understands what matters now, knows
when to hold back, and takes useful initiative with permission.

## Status — Sage Refresh

The repository contains the working foundation: local browser and terminal chat,
durable episodic event storage, lexical and embedding-assisted recall,
held-close privacy, separate interior storage, a Notebook drawer, background
extraction/reflection, and one bounded waiting-message surface.

The active work is contextual continuity: making the whole remembered history
help Sage respond naturally to the present conversation. The old V3 rebuild is
sealed as historical foundation work, not current product authority.

Run after configuring the local router:

```bash
python3 launch.py
```

This writes lived memory to `~/sage_data/`; back up existing lived memory before first use.

Sage uses an ordered talk-model priority: Qwen 3.8 Max, DeepSeek V4 Pro, then
DeepSeek V4 Flash. Set `SAGE_CHAT_MODELS` as a comma-separated list, or repeat
`--alias` to override the priority for a run. A failed or unusable model falls
through to the next one before Sage records an assistant reply.

## Model audition

Run the fixed Sage situations against one or more local-router aliases without
touching lived memory:

```bash
python3 tools/model_audition.py <alias> [<alias> ...] --output workbench/audition.json
```

Review each reply for continuity, warmth, restraint, honesty, naturalness, and
fit. The fixed set is also available without a router using `--self-check`.

## Principles

- Keep every accepted turn as episodic memory.
- Let present context decide what becomes relevant.
- Preserve source events, associations, patterns, and contradictions together.
- Use memory to give every capability continuity.
- Calibrate initiative: answer, notice, suggest, prepare, act, or hold back.
- Keep Sage local-first, owned, and privacy-aware.

## Project records

- [North Star](docs/NORTH_STAR.md)
- [Invariants](docs/INVARIANTS.md)
- [Decisions](docs/DECISIONS.md)
- [Current milestone](docs/MILESTONE.md)
- [Long-hold roadmap and whiteboard](docs/ROADMAP.md)
- [Project guide](AGENTS.md)

## Contributing

Project direction stays behavior-first. Start from the current milestone, trace
affected behavior, keep changes small, and provide real verification evidence.
