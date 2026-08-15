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
