# Sage — Current Milestone

## Status

Active: local browser chat with durable history and streaming.

## Foundation evidence

Terminal chat routes through a configured free-tier alias. Each accepted user and assistant turn persists as a separate timestamped event and survives restart.

## Active outcome

Sage runs as a local browser chat on `127.0.0.1`. Browser reload shows persisted history. A successful router stream appears progressively and persists one assistant event only after complete delivery. Terminal chat remains supported.

## Acceptance evidence

- `GET /` serves local browser chat; `GET /api/history` returns persisted events in chronological order.
- `POST /api/chat` routes only to configured alias through `localhost:20128/v1` and streams successful reply chunks.
- Accepted user event persists before router work.
- Complete successful stream persists exactly one assistant event; a failed, malformed, truncated, or client-abandoned stream persists no assistant event.
- Browser reload retains events.
- Provider failure reports clear failure and retains accepted user event.
- Terminal Foundation behavior and tests remain supported.
- No paid-model fallback exists.

## Verification

```bash
python3 -m unittest discover -s tests
```

## Excluded

Recall, embeddings, entities/graph, heartbeat/reach, threads, voice, beliefs, directive work, search, and held-close behavior remain excluded until later milestones select them.

## Next scope

Held-close firewall is next candidate only after browser chat acceptance evidence passes.
