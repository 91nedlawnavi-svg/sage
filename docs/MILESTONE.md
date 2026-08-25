# Sage — Current Milestone

## Status

Active: Full rebuild — Dual Storage, Multi-Model Routing, Vector Semantic Recall, Interior Notebook, and Heartbeat.

## Rebuild evidence

Sage v3 architecture is live and verified:
- Physical separation of relational world memory (`~/sage_data/relational/`) and interior data (`~/sage_data/interior/`).
- Multi-model routing using free-tier aliases (`SAGE_CHAT_MODEL` for conversation, `SAGE_SCRIBE_MODEL` for extraction/reflections) with reasoning preamble stripping and truncation guards.
- Local embedding support connected to `llama-embedder` (`127.0.0.1:8081`) for vector cosine similarity combined with BM25 term frequency.
- Entity observation intake (append-only observation events, zero locks, zero promotion queues).
- Interior presence: private reflections log, arguable belief ledger with evidence tracking, single revisable waiting message buffer.
- Lightweight web UI with Notebook drawer (reflections, beliefs, entities), held-close tap toggle, and chunked streaming.
- Systemd user service `sage.service` running `launch.py` with active heartbeat daemon.

## Boundary evidence

- User privacy classification is stored with the event before any embedding or provider-context assembly.
- Unknown or interrupted user intake is excluded from embeddings, recall-built provider context, and heartbeat work.
- Heartbeat entity extraction and reflections record durable source-event completion and are safe to retry.
- Belief records are read-only until an argument-and-evidence write path exists.
- Browser health does not disclose the configured model alias; browser time display is explicitly WIB.

## Verification

```bash
python3 -m unittest discover -s tests
```

## Excluded

Mutable state tables, promotion desks, fact locks, and push notifications remain permanently excluded per invariants.
