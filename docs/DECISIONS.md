# Sage — Decisions

Append dated decisions here. Do not rewrite earlier entries; add a later entry when direction changes.

## 2026-08-10 — Carried forward decisions

- Sage is a local, single-user persistent presence.
- Memory is timestamped events with two clocks: exact `said_at` and fuzzy or absent `happened_at`.
- Recall computes present meaning. Contradictions stay in history.
- Free-tier model routing only.
- Lived memory belongs in `~/sage_data/`; identity and code do not.
- Relational and interior memory stay physically separate.
- Held-close material is protected at recall time and excluded from background provider re-shipping.
- Reach is one revisable waiting message in app, never push notification.
- Beliefs may change only through argument and evidence.
- Directive work remains undecided and absent from repository.

## 2026-08-12 — Foundation implementation boundary

- Foundation uses a local terminal chat surface.
- Sage calls only `http://localhost:20128/v1/chat/completions` using a required configured free-tier alias.
- Event history is append-only JSONL at `~/sage_data/events.jsonl`; each record has `role`, `content`, and exact UTC `said_at`.
- User events persist before router calls. Assistant events persist only after valid successful replies.
- No router fallback, retry, alternate provider, or paid-model route exists.

## 2026-08-15 — Local browser chat runtime

- Sage serves its local browser chat from `127.0.0.1`; it does not bind a LAN-facing address.
- Browser chat uses Python's standard-library HTTP server and static local assets. FastAPI is not added for this slice.
- Browser and terminal chat share event persistence and local-router behavior.
- Browser replies use HTTP chunked streaming. Only a complete valid router stream becomes an assistant event.

## 2026-08-15 — Held-close firewall

- New conversation events receive stable IDs. Legacy Foundation event lines remain readable without rewrite.
- User intake writes an append-only local privacy classification after its user event. Browser hold/release writes a later append-only override.
- Effective held-close status is computed by replaying those privacy records in log order; no mutable privacy or current-state store exists.
- Held-close detection is deterministic and provider-free, with four later user turns of local carry after a strong signal.
- Held-close user input receives a fixed local acknowledgement and never enters a provider request. Release never retroactively ships an earlier held-close turn.

## 2026-08-16 — Query-aware recall foundation

- Router context is now built from historical events using query-aware keyword relevance and excludes held-close material by default.
- Recall is deterministic, append-only event-based, and does not change existing held-close behavior or add mutable privacy state.
- This is a temporary relevance fallback until embeddings are introduced for semantic scoring.

## 2026-08-16 — Recall precision hardening

- Add exact-phrase and stop-word-aware fallback inside `EventStore.recall` for higher precision before embeddings.
- Keep exclusion of held-close events and avoid using a mutable state table in recall.
- Multi-term recall now treats all-stop-word-only queries as context-fallback, preserving behavior for weak signals.

## 2026-08-17 — Scored term-frequency and phrase recall ranking

- `EventStore.recall` uses weighted scoring combining term overlap ratio, term frequency in event content, and exact phrase matching.
- Scoring maintains zero external dependencies and keeps held-close exclusion intact.
- Results are ranked by score descending without introducing a mutable state store or caching layer.

## 2026-08-17 — Full v3 Rebuild Completion

- Restored dual storage separation (`~/sage_data/relational/` and `~/sage_data/interior/`).
- Multi-model routing: `SAGE_CHAT_MODEL` (Mimo v2.5) for conversation, `SAGE_SCRIBE_MODEL` (Llama 3.3 70B) for extraction and reflections. Added `<think>` reasoning stripper and truncation protections.
- Integrated local `llama-embedder` (`127.0.0.1:8081`) with hybrid vector cosine + BM25 scoring.
- Event-based entity observations (no locks, no promotion queues).
- Restored interior reflections log, arguable belief ledger, and single revisable waiting message buffer.
- Added Notebook drawer UI in browser chat.
- Unified launch entrypoint (`launch.py`) with heartbeat daemon and restored `sage.service` systemd daemon.
