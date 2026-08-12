# Sage

Sage is a local, persistent presence for one person.

Memory is her main engine. She should carry continuity, hold open questions, and return changed by what happened — without becoming a memory database wearing warmth.

## Status

Foundation chat slice available: terminal chat through local free-tier router aliases with durable timestamped event persistence. Recall and embeddings remain excluded from current milestone.

Run after configuring a confirmed free-tier alias:

```bash
python3 src/sage.py --alias <alias>
```

This writes lived memory to `~/sage_data/`; back up existing lived memory before first use.

## Principles

- Local-first, single-user
- Free-tier inference routing only
- Timestamped events, not frozen facts
- Contradictions retained; present meaning computed at recall
- Separate relational and interior memory
- Held-close material stays held
- Quiet and reach must have a reason

## Project records

- [North Star](docs/NORTH_STAR.md)
- [Invariants](docs/INVARIANTS.md)
- [Decisions](docs/DECISIONS.md)
- [Current milestone](docs/MILESTONE.md)
- [Engineering contract](CLAUDE.md)

## Contributing

Project direction stays behavior-first. Start from current milestone, trace affected behavior, keep changes small, and provide real verification evidence.
