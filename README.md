# Sage

Sage is an owned, persistent personal intelligence for Elliot.

The long-term goal is JARVIS-like: a daily-life companion that remembers
everything, understands what matters now, knows when to hold back, and takes
useful initiative with permission. Single user, local-first, free-tier models
only.

## What Sage does today

**Conversation** — browser chat (mobile and desktop) with streaming replies,
sensitive-mode privacy, and new-chat boundaries.

**Episodic memory** — every accepted turn is appended as a timestamped event
in JSONL. Recall combines BM25 lexical search with cosine-similarity
embeddings, scored against the current exchange. Nothing is discarded for
being mundane.

**Self-authored identity** — Sage observes her own behavior during background
heartbeat passes and proposes identity claims. Elliot ratifies or rejects each
claim through the Notebook UI. Ratified claims compose into the system prompt,
giving Sage a self-description she earned rather than one that was written for
her.

**Autonomous metabolism** — after a configurable silence window (default 5
minutes), Sage scans the last conversation for gaps in her understanding,
searches the web to explore them, writes a digest reflection, and optionally
leaves a waiting message for Elliot's return. Each stage gates the next;
silence is the default outcome.

**Conversational search** — during a live conversation, Sage can decide to
search the web when she recognizes she lacks knowledge. Results are stored as
episodic events with source URLs.

**Privacy** — sensitive messages are excluded from recall, embeddings, provider
prompts, and background processing. Unknown privacy classification fails
closed.

**Interior storage** — reflections, entity observations, identity proposals,
metabolism records, beliefs, and one bounded waiting-message surface live in
`~/sage_data/interior/`, separate from relational event history.

**SQLite mirrors** — relational and interior databases are dual-written
alongside JSONL for indexed reads. JSONL remains the source of truth; mirrors
are derived and rebuildable.

**Background heartbeat** — runs every 120 seconds: entity extraction,
reflection, identity proposal, and metabolism trigger check, each with
retry-safe completion records.

**108 deterministic tests** covering the full foundation.

## Running

Configure `.env` from `.env.example`, then:

```bash
python3 launch.py
```

Sage starts on port 6969 with a heartbeat thread. Lived memory writes to
`~/sage_data/`; back up existing data before first use.

The systemd user service at `~/.config/systemd/user/sage.service` manages
production operation.

## Models

Free-tier only. The talk-model priority chain:

1. Qwen 3.8 Max (free)
2. DeepSeek V4 Pro
3. DeepSeek V4 Flash

A failed or unusable response falls through to the next model before an
assistant reply is recorded. Set `SAGE_CHAT_MODELS` as a comma-separated list
in `.env`. The local embedder is a separate fixed component.

## Model audition

Test fixed Sage situations against router aliases without touching lived
memory:

```bash
python3 tools/model_audition.py <alias> [<alias> ...] --output workbench/audition.json
```

Use `--self-check` to run without a router.

## Architecture

Python 3, stdlib only, no external dependencies.

- `launch.py` — entry point: loads `.env`, wires stores/router/heartbeat
- `src/sage.py` — message handling, directive loading, router message building
- `src/web.py` — HTTP server, chat API, streaming NDJSON replies
- `src/events.py` — append-only JSONL event store with hybrid recall
- `src/interior.py` — interior storage (reflections, identity, metabolism, waiting messages)
- `src/metabolism.py` — four-stage post-conversation pipeline
- `src/heartbeat.py` — background extraction, reflection, identity, metabolism
- `src/router.py` — LLM router client with model fallback chain
- `src/database.py` — SQLite mirror layer
- `src/search.py` — web search integration (SearXNG)
- `src/static/` — frontend HTML/CSS/JS
- `tests/test_foundation.py` — 108 deterministic tests

## Tests

```bash
python3 -m pytest tests/
```

## Project records

- [North Star](docs/NORTH_STAR.md) — purpose and felt outcome
- [Invariants](docs/INVARIANTS.md) — permanent constraints
- [Decisions](docs/DECISIONS.md) — settled choices
- [Milestone](docs/MILESTONE.md) — current work and acceptance evidence
- [Blueprint](docs/BLUEPRINT.md) — behavior map
- [Roadmap](docs/ROADMAP.md) — long-hold visual roadmap
