# Sage

Sage is an owned, persistent personal intelligence for Elliot.

The long-term goal is JARVIS-like: a daily-life companion that remembers
everything, understands what matters now, knows when to hold back, and takes
useful initiative with permission. Single user, local-first, free-tier models
only.

## What Sage does today

**Conversation** — browser chat (mobile and desktop) with streaming replies,
one normal message path, and navigable sessions that can be reopened, renamed,
archived, restored, and assigned `Auto` or one configured text model without
splitting Sage's lifetime memory. Completed replies show their actual model;
failed answers can retry the saved turn without duplicating it.

**Experimental voice** — `/call` uses Gemini 3.1 Flash Live Preview for a
direct, native audio-to-audio conversation. Sage supplies identity and recent
context, can recall relevant local events during the call, and saves completed
transcripts as voice-tagged episodic history. Later corrections remain linked
to the untouched original transcript and become the wording used by recall.
New calls can be inspected and corrected locally at `/calls`, grouped by call
and turn. `/call/split` follows the active chat session and its model by
default; its picker can persist an `Auto` or explicit voice override. It is a
side-by-side latency trial: Deepgram transcribes a
held recording, Sage's normal text path streams the reply, and completed
sentences are synthesized and queued while later text is still arriving. Audio
itself is not stored.

**Episodic memory** — every accepted turn is appended as a timestamped event
in JSONL. Recall combines lexical overlap and term frequency with
cosine-similarity embeddings, scored against the current exchange. Nothing is
discarded for being mundane.

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
search the web when she recognizes she lacks knowledge. Query and source
provenance are stored as separate relational search records, not chat messages.

**Provider boundary** — lived memory stays local. Current messages and compact
relevant context may pass through the configured router, or directly to Gemini
for the approved Live voice experiment. Sage has no sensitive or local-only
message mode.

**Separate storage** — events, embeddings, entity observations, search records,
and completion records are relational. Reflections, identity proposals,
metabolism records, and one bounded waiting message are interior material.

**SQLite mirrors** — relational and interior databases are dual-written
alongside JSONL. JSONL remains the source of truth; mirrors are derived and
rebuildable. Verification compares complete mirror contents against a fresh
temporary rebuild, not only row counts.

**Background heartbeat** — runs every 120 seconds: entity extraction,
reflection, identity proposal, and metabolism trigger check. Successful passes
use completion records to prevent duplicate work.

**155 deterministic tests** covering the current foundation.

## Running

Configure `.env` from `.env.example`, then:

```bash
python3 launch.py
```

Sage starts on port 6969 with a heartbeat thread. Lived memory writes to
`~/sage_data/`; back up existing data before first use.

To try `/call`, add a Google AI Studio key as `GEMINI_API_KEY` in `.env`. To try
`/call/split`, add `DEEPGRAM_API_KEY`. Sage keeps both keys server-side; only
the direct Gemini path gives the browser a one-use token.

The systemd user service at `~/.config/systemd/user/sage.service` manages
production operation.

## Models

Free-tier only. The talk-model priority chain:

1. Qwen 3.8 Max (free)
2. DeepSeek V4 Pro
3. DeepSeek V4 Flash

A failed or unusable response falls through to the next model before an
assistant reply is recorded when a chat uses `Auto`. Each text session can
instead select one configured model; that explicit choice never silently falls
back. Set `SAGE_CHAT_MODELS` as a comma-separated list in `.env`. The local
embedder and background routes remain separate components.

## Model audition

Test fixed Sage situations against router aliases without touching lived
memory:

```bash
python3 tools/model_audition.py <alias> [<alias> ...] --output workbench/audition.json
```

Use `--self-check` to run without a router.

## Current system map

### Stack

- Python 3 standard library for the application.
- Plain HTML, CSS, and JavaScript for the browser interface.
- Host and Origin checks allow local HTTP access plus the exact HTTPS Tailscale
  Funnel at `th.tail674e3a.ts.net`.
- JSONL files as the permanent memory record.
- SQLite as a rebuildable copy, not the source of truth.
- Local services for model routing, embeddings, and web search.
- Gemini Live for direct audio and Deepgram STT/TTS for the split voice trial.
- systemd for starting and restarting Sage.

Sage is one small application with one background thread. It is not a group of
microservices.

### Layers

| Layer | Current home |
|---|---|
| **Interface** | Browser chat, Notebook, and the voice trial in `src/static/`, served by `src/web.py`. |
| **Sage Core** | Context and identity in `src/sage.py`; browser flow and search decisions still live in `src/web.py`. |
| **Memory** | Events and recall in `src/events.py`; interior material in `src/interior.py`; SQLite copies in `src/database.py`. |
| **Intelligence** | Talk-model failover and local embeddings in `src/router.py`; Gemini Live for the experimental native-audio path. |
| **Capabilities** | Web search through `src/search.py`. |
| **Agency** | Background reflection and exploration in `src/heartbeat.py` and `src/metabolism.py`. |
| **Operations** | Startup in `launch.py`, systemd, health checks, tests, and maintenance tools. |

The Core is not one clean boundary yet. Production browser behavior is split
between `src/sage.py` and `src/web.py`.

### Architecture

A normal conversation follows this path:

1. Browser sends Elliot's message to Sage.
2. Sage saves the message before contacting any model.
3. Sage builds context from recent conversation, relevant older events, the
   identity seed, and ratified identity entries.
4. Sage may search the web and add the results as temporary context.
5. The local router uses the session's explicit text model, or tries the talk
   models in order for `Auto`.
6. A complete reply streams to the browser, records its actual model, and is
   then saved. A failed answer can retry the already-saved user event.

Starting a new chat adds a boundary with a new stable session ID; it does not
delete old events. Legacy boundaries define sessions at read time without
rewriting old history. The chat drawer lists those sessions and can reopen,
rename, archive, and restore them. Session control changes are appended as new
records. Archived events remain available to global recall. Background work
follows a separate path: conversation, reflection, optional exploration, and
at most one waiting message.

Permanent deletion requires a current scope preview and exact typed `DELETE`.
If source data changes, confirmation must be repeated against a fresh preview.
Incomplete older provenance or derived records shared with other chats block
deletion; archive remains available. Successful deletion removes the listed
active local records and rebuilds both mirrors, with restart recovery for a
committed interruption. Surviving legacy event/session identities depend on
`relational/legacy_ids.json`; preserve that file with data backups. External
backups, provider context and other retained chats are outside the purge.

The `/call` trial keeps audio on a separate low-latency path: Sage issues a
short-lived token, then the browser streams audio directly to Gemini Live.
Gemini can request relevant events through Sage's local recall endpoint.
Completed input and output transcripts return to the normal event and embedding
path with voice provenance. Append-only correction records can replace faulty
wording for recall without rewriting the provider transcript. Future events
carry call and turn identifiers so `/calls` can present them together. The text
router and conversational web search are not used during direct Gemini calls.

The `/call/split` trial uses the opposite tradeoff. The browser sends each held
utterance to server-side Deepgram STT, then sends its transcript through Sage's
normal routed text path. As reply text streams back, sentence-sized chunks are
sent to server-side Deepgram TTS in parallel and played in order. The page shows
STT, first-sentence, TTS, and total-to-audio timing. Split turns use the same voice provenance
and Call Review path as direct calls.

### Modules

- `launch.py` — starts and connects the production system.
- `src/web.py` — browser server, text and voice memory bridges, live chat flow,
  and Notebook APIs.
- `src/sage.py` — message acceptance, context building, directive, and an unused
  command-line chat path left from earlier development.
- `src/events.py` — conversation history, related memory, and recall.
- `src/interior.py` — reflections, identity, metabolism records, and waiting message.
- `src/database.py` — rebuildable SQLite copies.
- `src/router.py` — talk models and local embeddings.
- `src/search.py` — local web search.
- `src/heartbeat.py` — schedules background work.
- `src/metabolism.py` — explores gaps after conversation becomes quiet.
- `src/static/` — browser chat, voice call, Call Review, and Notebook.
- `tools/` — backfill and model-checking utilities.
- `tests/` — 121 deterministic checks.

## Tests

```bash
python3 -m pytest tests/
```

## Project records

- [Blueprint](docs/BLUEPRINT.md) — purpose, behavior, architecture, and boundaries
- [Decisions](docs/DECISIONS.md) — settled choices
- [Milestone](docs/MILESTONE.md) — current work and acceptance evidence
- [Timeline](docs/TIMELINE.md) — visual past, present, and future context
