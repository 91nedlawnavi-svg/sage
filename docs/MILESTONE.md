# Sage — Current Milestone

## Status

Not started.

## Outcome

Prove smallest chat vertical slice: route a message through a free-tier alias and persist each accepted user and assistant turn as timestamped events.

## In scope

- One local chat interaction
- Free-tier model alias routing
- Local persistence for accepted user and assistant turns
- Exact UTC `said_at` timestamps
- Verification that persisted turns survive process restart

## Acceptance checks

- A user message reaches configured free-tier alias.
- Successful assistant reply is shown to user.
- Accepted user and assistant turns persist as separate timestamped events.
- Restart retains persisted events.
- Provider failure reports clear failure and preserves no false assistant event.
- No paid-model fallback exists.

## Explicitly excluded

- Recall and embeddings
- Entities and graph
- Heartbeat or reach
- Threads
- Voice
- Beliefs
- Directive work

No implementation has begun.
