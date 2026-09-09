# Sage — Current Milestone

## Sage Refresh

The old V3 rebuild is sealed as historical implementation work. It is not the
current product definition. The current work is to make the existing foundation
serve the refreshed picture: an owned personal intelligence whose whole-life
episodic memory becomes relevant continuity.

## Current reality

Already present and verified:

- Local browser chat through the configured local router. An older command-line
  chat path remains in code but is not a current product interface.
- Browser host and Origin checks permit local HTTP access and the exact HTTPS
  Tailscale Funnel at `th.tail674e3a.ts.net` without trusting other domains.
- Durable UTC event history with restart persistence.
- Lexical and embedding-assisted event recall.
- One normal conversation path for every accepted message; Sensitive mode and
  its automatic provider exclusions are retired.
- Separate relational and interior storage.
- Reflections, entity observations, and one waiting-message surface.
- Restored daily-life frontend with Notebook drawer and streaming chat.
- Background extraction/reflection with successful-pass completion records.
- Refreshed directive injected into foreground browser and terminal chat.
- Ordered talk-model failover: Qwen 3.8 Max, DeepSeek V4 Pro, then DeepSeek V4 Flash.
- Recall cues built from the recent eligible exchange plus the newest message.
- Resumed-session context has a tested bounded contract: selected-session tail,
  a two-event recent-life bridge on first resume, then global recall inside the
  existing eight-event ceiling.
- New text and voice events carry stable session IDs across restarts. New-chat
  boundaries start new IDs; legacy boundaries define deterministic sessions at
  read time without rewriting old event history.
- Browser session navigation lists, opens, continues, renames, archives, and
  restores chats. Control changes append new records; archive does not remove
  events from global recall. Keyboard focus, mobile touch targets, and semantic
  labels are built into the drawer.
- Browser text chat offers `Auto` or one configured model per session. Explicit
  choices never silently fall back; completed replies retain and show the model
  that actually answered. A clear Model error row can retry the same saved user
  event, including a one-attempt Auto override, without duplicating lived
  history.
- SQLite mirrors (relational and interior) dual-written alongside JSONL with fail-soft recovery.
- Conversational web search: Sage decides before replying when to search and
  stores query/source provenance separately from conversation events.
- Self-authored identity: heartbeat proposes identity claims from self-observation, Elliot ratifies/rejects via Notebook UI, ratified claims compose into system prompt.
- Autonomous metabolism: post-conversation gap scan, web exploration, digest reflection, and waiting-message reach. Each stage gates the next; silence is the default.
- An experimental `/call` path for native audio-to-audio conversation. Elliot
  verified the live microphone, spoken response, name recognition, and basic
  felt quality. The memory bridge now supplies ratified identity, recent
  context, and on-demand episodic recall; completed transcripts enter normal
  event history with voice provenance while audio remains unstored. Later
  corrections are linked without replacing original transcripts, and corrected
  wording drives recall. New calls and turns are grouped in a local review
  screen where Elliot can inspect and correct either side of the transcript.
  A separate `/call/split` latency trial sends held audio through Deepgram STT,
  Sage's normal streamed text path, and sentence-chunked Deepgram TTS. It shows
  STT, first-sentence, TTS, and total-to-audio timing while leaving direct Gemini Live
  unchanged as the baseline. Split voice now inherits the active chat session's
  model by default, with a persisted `Auto` or explicit voice override; reopened
  sessions keep voice turns in the selected session.
- Routed split-voice timing was rechecked twice per configured model: Qwen 3.8
  Max produced 0/2 valid streamed replies, DeepSeek V4 Pro measured 8.53s
  median, and DeepSeek V4 Flash measured 5.08s median to the first complete TTS
  audio blob. This backend probe excludes microphone capture and STT; the live
  screen reports those stages separately.
- 132 deterministic tests passing across the current foundation, both voice
  paths, and temporary-data deletion checks.
- Permanent session deletion previews exact local scope, requires typed
  `DELETE`, removes directly and safely-proven derived records, cleans SQLite
  mirrors, and discloses the external-backup boundary. Tests cover interruption,
  restart, recall removal, voice corrections/call grouping, and derived records.
- `SAGE-021` integration review passed: production service is active at commit
  `8033584`, `/health` returns `{"ok": true}`, live browser controls render,
  deletion preview returns exact counts and backup disclosure, browser console
  has no errors, and the full suite remains green.

These are foundations, not proof that Sage already feels like a JARVIS-like
personal intelligence.

## Active outcome — felt continuity

Sage's remembered history influences replies naturally in ordinary, non-crisis
conversations. Related events can be grouped or summarized without erasing
their sources.

## Acceptance evidence — felt continuity

- Relevant older moments influence replies without sounding like search.
- Contradictory events remain available without forced resolution.
- Related moments can be grouped without replacing their original events.
- Ordinary conversation with Elliot provides a felt test, not only technical
  retrieval scores.

## Explicitly not active yet

- Broad autonomous actions beyond web search.
- External notifications.
- Production voice or ambient interfaces.
- A belief model or belief-edit workflow.
- A graph or current-state replacement for event memory.
- More background activity merely to appear alive.
