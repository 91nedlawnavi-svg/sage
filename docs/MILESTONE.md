# Sage — Current Milestone

## Status

Active: semantic event recall ranking and precision hardening.

## Foundation evidence

Terminal and browser chat route through a configured free-tier alias. Each accepted user and successful assistant turn persists as a separate timestamped event. Browser chat is local-only, durable across reload, and streams complete valid router replies.

## Active outcome

Sage provides query-aware context recall for provider calls while preserving held-close exclusion. Recall scores historical events using term overlap, term frequency, and exact phrase match ranking.

## Acceptance evidence

- Every new user message gains a stable event ID and one append-only local classification event.
- Existing Foundation event lines remain readable without migration or rewrite.
- Effective held-close state is computed by replaying classifications and later user overrides; no mutable privacy or current-state store exists.
- A held-close turn persists locally, receives a fixed local acknowledgement, and never reaches `localhost:20128/v1`.
- Browser and terminal share that provider firewall.
- Browser history exposes effective state; same-origin hold/release controls append only a privacy override event.
- Canary tests prove held-close content never enters any currently implemented provider request.
- Open turns retain Foundation router and durability behavior. No paid-model fallback exists.
- Scored recall ranks exact-phrase and high-overlap/high-frequency terms ahead of weaker keyword hits.

## Verification

```bash
python3 -m unittest discover -s tests
```

## Excluded

Embeddings, entities/graph, extraction, heartbeat/reach, threads, voice, beliefs, directive work, search, background provider work, and mutable privacy/current-state stores remain excluded.

## Next scope

Entity candidate detection and continuity linking are next candidates.
