# Sage — Decisions

This file records the current settled frame. Earlier V3 decision text remains
available in Git history, but is superseded and must not be treated as current
product authority.

## 2026-08-25 — Sage Refresh

- Sage is an owned, persistent personal intelligence for Elliot, with a
  long-term JARVIS-like goal: broad usefulness, continuity, judgment,
  calibrated initiative, and local ownership.
- Sage is for daily life as a whole. Serious, ordinary, silly, unfinished, and
  apparently insignificant turns all belong in memory.
- Every accepted turn is episodic history with exact UTC time. Retention does
  not require an intake decision that the moment is important.
- Recall reconstructs present relevance. It may use lexical, semantic,
  temporal, episodic, entity, and pattern signals, but source events remain
  durable and contradictions remain history.
- Episodes, associations, summaries, and patterns are derived and provisional.
  They retain provenance and can be revised by later evidence.
- Sage's agency is calibrated: she may answer, notice, mention, suggest,
  prepare, or act. Authorization and risk determine the boundary.
- Local ownership, minimized provider context, graceful provider failure, and
  the separation of lived memory from code remain permanent constraints.
- The old V3 rebuild is sealed as historical foundation work. The Sage Refresh
  supersedes its product framing without discarding its useful implementation
  or lived memory.

## 2026-08-25 — Current implementation boundary

- The current repository is a working foundation, not a completed personal
  intelligence. Its next behavior-sized outcome is context-aware continuity.
- Heartbeat extraction and reflection may run locally through the configured
  router, but heartbeat activity is not itself initiative or proof of agency.
- In-app waiting messages remain the only bounded initiative surface for now.
- No direct belief-edit path, push path, or broad autonomous action path exists.

## 2026-08-26 — Talk-model priority

- Sage does not depend on one permanent talk model.
- The current priority is Qwen 3.8 Max, DeepSeek V4 Pro, then DeepSeek V4
  Flash.
- A failed, empty, malformed, reasoning-only, or incomplete response falls
  through to the next priority before an assistant event is saved.
- The local embedder remains a separate fixed memory component; it is not part
  of talk-model selection.

## 2026-09-06 — Sensitive mode retired

- Sensitive mode, `/sensitive`, automatic classification, carry state, privacy
  overrides, and provider-exclusion flags are removed.
- No replacement private or local-only writing control is added.
- Sage handles difficult or intimate situations through normal conversation,
  using the same memory, embedding, search, provider, and background paths as
  other accepted messages.
- Lived memory remains locally owned and provider context remains minimized,
  but Sage does not promise that conversation content stays local-only.
- Existing append-only records are preserved. Legacy privacy metadata may
  remain in old files but no longer changes runtime behavior.

## 2026-09-06 — Four canonical product records

- `BLUEPRINT.md` defines purpose, intended behavior, system layers,
  architecture, interface principles, and permanent boundaries.
- `DECISIONS.md` records settled choices and why they changed.
- `MILESTONE.md` records the active outcome and its acceptance evidence.
- `TIMELINE.md` is Elliot's horizontal past/now/next context map.
- The former North Star, Invariants, Product, and Roadmap records were merged
  into this smaller set. `README.md` and `AGENTS.md` remain operational entry
  points, not additional product authority.

## 2026-09-06 — Current architecture framing

- Sage currently runs as one small Python application with one background
  thread and local supporting services for models, embeddings, and search.
- The browser chat and Notebook are the real user interface. The command-line
  chat path is leftover development code, not a current product interface.
- The current Sage Core is split: context and identity live in `sage.py`, while
  browser flow and search judgment remain in `web.py`.
- The implementation map reports this present truth. It does not pretend the
  intended clean layer boundary already exists.

## 2026-09-07 — Isolated Gemini Live voice trial

- `/call` is an experimental voice surface beside normal browser chat on the
  same Sage server and port.
- Gemini 3.1 Flash Live Preview is the temporary audio-to-audio call engine. It
  is not treated as Sage's permanent mind or as a replacement for text chat.
- Sage's backend keeps the Google API key and issues the browser a one-use,
  short-lived token. The token also locks the model, voice, and identity seed
  on the server. Live audio then travels directly between the browser and
  Gemini to avoid a Python audio relay.
- This direct provider connection is a narrow experiment outside the normal
  text router path. It is not a settled production provider design.
- The trial receives only `directive.txt`. It has no recall, ratified interior
  identity, tools, search, transcript storage, or writes to `~/sage_data/`.

## 2026-09-07 — Voice memory bridge

- Elliot's live test established that native audio conversation works and feels
  worth connecting to Sage's continuity.
- Direct browser-to-Gemini audio remains the low-latency lane. Python does not
  relay audio.
- A separate memory lane now supplies Sage's identity seed, ratified identity,
  recent visible conversation, and an on-demand local episodic recall tool.
- Only the relevant recalled events cross the direct Gemini connection. Lived
  memory remains locally stored.
- Finalized user and assistant transcripts are appended as normal events and
  receive the normal embedding and background treatment. Audio is not stored.
- Gemini Live remains the full response model for this experimental voice path,
  not merely a mouth for the routed text model. The normal text router, web
  search, and broader tools are not part of calls yet.

## 2026-09-07 — Voice transcript provenance and correction

- New conversation events identify whether they came from text or voice.
  Existing untagged history remains unchanged.
- A voice transcript correction is a new record linked to the source event.
  The original provider transcript and every correction remain append-only.
- The newest correction supplies effective wording for recall, embeddings,
  recent context, and background understanding while original wording remains
  available for inspection.
- A review and evaluation surface is separate later work. Reliable provenance
  and correction history come first.
