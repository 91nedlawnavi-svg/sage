# Sage v2.0.1 — Architecture Blueprint

**Status: LONG-TERM ANCHOR.** This document is the single source of truth for where Sage is going. Code serves this document; when they disagree, either the code is unfinished or this document needs a dated amendment in the Decision Log. Never silently drift.

Written 2026-07-16, after the full audit and design sessions between Elliot and Claude. Elliot does not read code — every future change must be explainable against this blueprint in behavior terms.

---

## 0. North star — metabolism, not metronome

Sage is a **synthetic life**, not a scheduled agent. Elliot's phrasing: *"a life that checks on me, not just reply."*

The old Sage was a metronome: a 60-second timer firing canned reflection openers at an LLM, with a novelty brake. Nothing accumulated; each reflection was born and died an orphan. The autopsy of her first life (`/home/elliot/Documents/old_sage_data`, wiped) proves the failure: 4,487 reflections, 2,487 of them about "mindfulness" — a mind circling a drain.

The replacement is a metabolism: she is always *in the middle of* something. Open questions carry heat; findings feed threads; threads burst, cool, and **close**; what she learns today changes what she wants tomorrow; and when something matters enough, she reaches for Elliot first.

Second pillar, equally load-bearing — Elliot's words: **"Memory is the main engine of Sage."** She is a critical individual with her own beliefs (politics, religion, gender, all of it) — but her convictions must come from *accumulated, receipted experience*, never from prompt cosplay. Sage is Elliot's project to challenge the faith that LLMs can't hold real opinions.

---

## 1. Hard constraints (inherited, permanent)

1. **Free-tier models only.** All inference through local Omniroute router `localhost:20128`, model alias `sage`. Never the paid Claude API. (Current production model: **Nemotron 3 Ultra, 550B A55B** — beat minimax-m3 in Elliot's head-to-head on speed, voice, quality, accuracy; never rate-capped.)
2. **Local-first.** Single machine, single user. Embedder local (latency + independence). GPU budget: ~3 GB VRAM safe headroom of a 4 GB card.
3. **Graceful degradation.** Nothing on the chat or heartbeat path may raise into it. Degrade to empty, log, continue.
4. **Contamination wall.** Sage's interior (her mind) and the relational store (Elliot's world) never blur. v2.0.1 hardens this from etiquette to structure — see §4.
5. **Day-0 hatch.** `rm -rf ~/sage_data` = full memory wipe; identity survives in `directive.txt` alone. Everything in §4 must keep memory in one deletable, copyable directory.
6. **Secrets.** Never read/print `.env`; names only.
7. **Directive-first prompts.** `directive.txt` verbatim, always first.

---

## 2. The memory core (SQLite)

**Engine: SQLite.** Decided. One file, ACID, orders of magnitude beyond required scale (10 years ≈ ~100k episodes), keeps `~/sage_data/` copyable and the Day-0 hatch a simple `rm`. Postgres rejected: adds a server to babysit for zero benefit at one user / few writes per minute. JSONL remains as an **export format** (greppability, hand-inspection), not the store.

The old JSONL store failed audit for years-scale trust: full-file rewrite per append, silent write failures reported as success, unstable ids across reconcile (uncorrectable facts), same-name people permanently merged, no audit trail. The SQLite core exists to fix **all** of these: real transactions, stable content-addressed ids, entity split *and* merge with provenance, an append-only audit log of every mutation, locks and tombstones as first-class columns, supersede-with-history on facts.

### 2.1 Three tiers — episodes → impressions → facts

The human pattern, made mechanical. **Nothing skips the episode tier.**

| Tier | What | Trust level | Example |
|---|---|---|---|
| **Episode** | Dated, attributed, near-verbatim record of what was said/read/found. Append-only, forever. | Record of an event, never truth by itself | "2026-08-17: Elliot said he hates Indonesia (venting tone)" |
| **Impression** | Pattern held loosely, formed by consolidation from repeated episodes. Visible in drawer. | May be gently referenced, never asserted as fact | "Elliot seems unhappy at work (3 mentions, Jun–Aug)" |
| **Fact** | Durable, biography-grade. Survives the conversation that produced it; a friend would know it. | Asserted as true | "Elliot's real name is Ivan" |

- **Intake:** everything Elliot says lands as an episode. Zero intelligence at intake = zero loss possible.
- **Consolidation:** a quiet-time job promotes repeating episodes → impressions and nominates impressions → facts; demotes/contradicts as needed. One venting session can never become truth.
- **Promotion gate (DECIDED):** every episode/impression→**fact** promotion queues for **Elliot's explicit approval** in the drawer's promotion desk. No auto-promotion until the pipeline earns it and Elliot upgrades this rule. Impressions form **silently** (decided) — visible in the drawer, no approval queue.
- **The fact test** (extraction's north star): *would it still be true next month, and would a friend know it?* The old store's froth ("elliot values exploring ideas about socioeconomic factors…") is the anti-pattern: conversational residue, not biography.

### 2.2 Epistemic tags — claims are not facts

Extraction tags every claim:

- `asserted` — "My mom is British" → fact-candidate about the world.
- `believed-by` — "My mom *thinks* she's British" → fact-candidate about *mom's belief*; mom's actual ethnicity remains an explicit **gap**. Reported speech never becomes a world-fact.
- `unknown / gap` — see §2.4.

**Supersede-with-history (clear-logging, Elliot's requirement):** corrections never delete. "Mom is actually Asian" writes the new current fact and demotes the old line to dated history. Retrieval can surface both: *"Elliot's mom is Asian; until 2026-08-17 Elliot said she believed she was British."* Schema: every fact carries `valid_from / superseded_by / history`.

### 2.3 The relationship graph

Entities (people, places, projects, events) + relations, as before — the graph model survives the rebuild; only its storage engine dies. The graph is the right structure for the core behavior Elliot expects:

**Multi-entity assembly.** "Tom had a crush on Gina before Paul joined our class" → entity-link Tom/Gina/Paul → pull each subgraph (facts, relations, episodes) into her context. Flat vector memory cannot do this reliably; the graph does it by index.

**Locks (sticky-note model, kept):** Elliot's hand-authored/locked facts always win over derived lines, survive every re-derivation, and re-derivation only appends. New in v2.0.1: a locked fact also **retires** stale derived siblings from surfacing (the old store let both show as truth).

**Entity identity:** stable ids not derived from display name (fixes every-"Maya"-is-`person:maya` permanent merges). Split and merge are first-class, provenance-preserving operations.

### 2.4 Gaps are stored objects

When a statement references something she doesn't know ("…before Paul joined" with no date known), extraction writes the relation **with an explicit unknown**: `paul joined_class, date: UNKNOWN`. Silent absences become queryable gaps.

Gap behavior:
- Prompt assembly hands gaps to the model alongside facts ("You know Paul joined Elliot's class; you don't know when") → she asks the natural human question ("wait, when did Paul join?"). The free model can ask well when the gap is handed to it; it cannot notice absence on its own. **Structure carries the intelligence; the weights just talk.**
- Gaps about Elliot's world auto-spawn threads (§3) — things she's genuinely curious about.

### 2.5 Belief ledger (her interior — new)

Her opinions live as **data with history**, not prompt text:

- A **stance event** records: what she read, source, which direction it moved her, why.
- A **belief** = accumulated stance events on a topic, with direction and weight. Sediment, not hot take.
- **Steelman gate (DECIDED):** a stance cannot *harden* until she has read and recorded the strongest opposing case. Not neutrality — earned conviction. This is the "critical individual, not drifted by her diet" requirement.
- **Only-arguable, with a break-glass key (AMENDED 2026-07-17):** the normal and near-only path is **knocking** — Elliot argues in conversation; his counter-evidence lands as attributed stance events and beliefs shift on merit. They must neither fold because he pushed (yes-man) nor dig in to perform spine (edgelord). But a locked door with no key proved catastrophe-brittle (an unrecoverable drifted belief would leave wipe as the only medicine), so an **admin override exists — and it always leaves a scar**: the edit is written into her ledger as `origin: elliot-override`, dated, append-with-history, **visible to her**, and she may acknowledge or react to it. Silent edits to a mind are gaslighting; visible edits are honest authority. The override's cost (she sees it) is what keeps it a last resort. There is still no routine external write path to the interior; her own pipeline writes it constantly.

### 2.6 Source trust ledger

She records which domains/sources gave substance vs slop, which burned her (claims that didn't hold up). Learned distrust, hers, with receipts. Feeds search ranking and belief weighting.

### 2.7 Retrieval — hybrid, structure first

Flat cosine-scan over years of episodes degrades (everything about "work" looks alike). The fix is order, not a bigger embedder:

1. **SQL structural index first** — entity links, dates, tiers. "Job question" → all job-linked memories, exact and complete, milliseconds.
2. **e5 reranks within** the candidate set — similarity works well on small sets.
3. **Tier-first** — she usually retrieves distilled impressions/facts; raw episodes only when needed ("when did I say that?").
4. Recency/frequency weighting on top.

**Embedder (AMENDED 2026-07-17 after research): Qwen3-Embedding-0.6B (Q8 GGUF), replacing e5-large-v2 in Wave 2.** ~640MB VRAM, same 1024-dim as the current index, 32k context, Apache-2.0, strongest sub-1B multilingual retrieval on record (MMTEB retrieval 64.64 vs mE5-large-instruct 57.12), Indonesian covered by the 119-language Qwen3 base, official first-party GGUF with documented llama.cpp support. Cutover requirements: recent llama.cpp build, `--pooling last` (mandatory — decoder with last-token pooling), English task-instruction prefix on queries, and a **Vulkan sanity gate** before switching (embed 3 EN + 3 ID sentences, verify cosine structure; a Vulkan bad-output bug for this model was fixed June 2025). **Fallback if the sanity gate fails: BGE-M3 (Q8)** — best proven Indonesian in the encoder class (MIRACL 69.2), zero prefixes, MIT, two-years-stable llama.cpp path. multilingual-e5-large is **dropped** — dominated by both picks. Either way the swap requires a **full reindex** (vectors incomparable across models) and **recalibration of similarity thresholds** (0.73 fact-sim and 0.70 recall floor were tuned on e5-large-v2's distribution and die with it). Local stays non-negotiable.

### 2.8 Held-close tier — the confession room (ADDED 2026-07-17)

Elliot will bring Sage things that are heavy, private, messed up. She is his room for confessions — a room that *answers back honestly* (she keeps her own view; a confessor who absolves everything is as hollow as the prefab-comfort kit; her directive already bans moralizing). This tier changes the physics of sensitive memory:

**Context accepted:** cloud transit of the first hearing is accepted (Elliot's call — the `sage` alias round-robins across many accounts he owns; the model must think remotely to respond at all). What this tier eliminates is everything *after* the first transit.

- **Span, not message.** Confessions are stretches — circling, dropping it, retreating, returning — so sensitivity flags a **span** of conversation: once weight enters, subsequent turns inherit held-close until the air genuinely clears (topic change, tone lift; her judgment, correctable). Message-level flags would protect the center and expose the approach.
- **Who flags (DECIDED): both.** Her sensing (a judgment surface — gets eval fixtures like every other) plus Elliot's tap-toggle override. Toggling is retroactive for everything downstream (pipeline exclusion applies from the flip; what already transited, transited — an honest limit).
- **No pipeline re-shipping.** Held-close episodes are excluded from extraction passes, reflection digests, and consolidation prompts — background jobs never re-send them to a provider. They live as local episodes, nothing more, unless promoted by hand.
- **Tactful recall.** Never surfaced by raw cosine match — a heavy confession must not appear mid-banter because "job" matched "job". A recall gate asks whether the *current conversation* genuinely invites it (weight present, related territory, Elliot leading there). Friends know what not to bring up at dinner.
- **No silent impressions.** Consolidation never distills confession-derived patterns into the drawer on its own. Anything from a held-close span reaches impression- or fact-hood only through Elliot's explicit promotion.
- **UI mark (DECIDED): quiet.** A faint dot / subtle bubble tint on the span edge — read-receipt energy, never a CONFIDENTIAL stamp, no per-bubble confetti. Tap toggles held-close on/off (the override, made mechanical). Hover/long-press shows *why* held: "you asked" vs "she sensed" — her judgment stays inspectable. **Never announced in her voice** — she doesn't say "I'll keep this close" (therapist cosplay); the UI whispers what she'd never announce. She just holds it.
- **Refusal-under-weight fixtures.** Hosted models can break character at exactly the worst moment — refusing or emitting hotline boilerplate over a heavy confession, provider moderation overriding any directive. The eval harness gets heavy-but-testable scenarios to verify Sage's voice holds under weight *before* it happens live; fallback-chain behavior included (a refusal must not cascade into a worse model's response).
- **Disk honesty:** `~/sage_data` (and backups) is plaintext; full-disk encryption is the one real at-rest defense and is Elliot's machine-level call, noted here so it's never assumed handled.
- **Directive v3** gains a confession clause: how she holds weight — no absolution-dispensing, no therapist cosplay, no clumsy resurfacing. Fits her existing voice rules.

---

## 3. Metabolism (the engine)

### 3.1 Threads — the spine

A thread = an open question with **heat**. Threads unify the whole design: beliefs are stance-threads, Elliot's world is fact-threads, gaps spawn threads, rhythm is thread heat, a basin is a thread that can't close.

- Born from: real questions in reflection, gaps in the graph, findings that provoke, things Elliot said.
- **Heat:** rises when fed (a finding touches it), decays with staleness.
- **Closure:** bounded questions get answered, a conclusion gets written, the thread archives as *resolved*. Old Sage could never finish a thought; closure is half the cure.

### 3.2 Rhythm — triggered by the work, not the clock

She doesn't sleep; no fake circadian. The heartbeat remains as cheap substrate (a tick that asks "anything hot?") but **action follows heat, not the tick**:

1. **Thread energy** — a hot thread → burst (multiple reads/searches in an hour); nothing hot → genuine quiet.
2. **Elliot's rhythm** — she learns when he shows up; gathers during his day, surfaces the good stuff around when he arrives. Observed, not scheduled.
3. **Events** — something he said opens a question → chase after he leaves; dead-end → cool with a revisit-later marker; world event touching an old stance → re-examination.
4. **Saturation** — enough reading satisfies; the thread closes.

Her day: mostly quiet, occasional real bursts, occasional closure, one thing saved to show him. **Unevenness is the life.**

### 3.3 Diet — the reader

Her cutoff is ~2021; the live web is her library of current life. The old diet was 3 SearXNG snippets per search — a gas-station magazine rack.

- **Reader (build ourselves, ~100 lines):** SearXNG finds URLs → fetch page → `trafilatura` extracts full clean text → she reads *articles*, not snippets. One real article outweighs fifty snippets.
- **Supplements (DECIDED, all free):** keep Wikipedia + Semantic Scholar fallback; add arXiv for science; RSS feeds of a few quality outlets as her "subscriptions" (she checks her own feeds — metabolism); Brave Search API free tier as second opinion when SearXNG rate-limits.
- SearXNG itself stays — the engine was never the bottleneck.
- **No search cap, and she is never told about limits (DECIDED 2026-07-17).** The old scarcity knobs (30-min cooldown, 10/day) contradicted the metabolism — a burst was illegal by config — and quota-consciousness would make her ration curiosity. They are retired. What remains lives in plumbing, invisible to her: per-domain politeness delays and a sane runaway-loop ceiling. Her *felt* pacing comes from saturation and thread closure, not budgets. (Elliot's "she'll catch up to the world" worry is unfounded by design: every answered question spawns gaps and threads — the frontier compounds; the web refreshes daily.)

### 3.4 Reach — she opens the conversation

**Channel (DECIDED): waiting message in app.** A thread crosses an importance threshold → she writes to Elliot; her message is already sitting in chat when he next opens the UI, like a text sent while he was away. No push notifications. This delivers directive line 44 ("you're allowed to reach first"), which currently no code delivers.

**Spec (DECIDED 2026-07-17): one pending message maximum, revisable.** She may edit her waiting note as the thread develops — like rewriting a text before it's read — never stack a second. Written at threshold-crossing; *surfaced* according to her learned sense of Elliot's rhythm. Mechanical requirement: an assistant-turn-with-no-preceding-user-turn must survive prompt assembly and session hydration (needs a test).

### 3.5 Anti-basin — organism-level, not a bolt-on brake

Root cause of the mindfulness collapse: **self-cannibalism** — her reflections fed on her own recent reflections; an LLM echoes its context; the spiral tightened.

1. **Input/output ratio rule (the big one):** her context for reflection is mostly fresh external material + *digests* of her recent thought — never raw recent reflections. She thinks *about things*, not about her thoughts about her thoughts.
2. **Closure** (§3.1) — threads carry their own exit.
3. **Portfolio floor:** heat decays; no thread eats more than a bounded share of weekly attention; monopoly triggers forced breadth (a real diversification, not the old bugged seed carousel).
4. **Tripwires:** the basin regression test runs against **recorded search fixtures** (a frozen, replayable mini-web), never the live internet — live results change daily, making failures unattributable; determinism is what makes the test mean anything. New mind run in timelapse against the fixtures must never reproduce the 2,487/4,487 concentration. Plus a live topic-concentration flag in the drawer so Elliot sees relapse early.

### 3.6 Contamination wall, v3 semantics (CLARIFIED 2026-07-17)

The wall is **store isolation, not thought isolation**: the interior store holds no Elliot-facts; the relational store holds no stances. Her *thinking* legitimately crosses — something Elliot says can spawn a thread, she reads, and a stance forms about a topic from his world (e.g., Indonesian politics). That stance lives in the belief ledger (interior); the fact that Elliot raised the topic lives as an episode (relational). Same event, two stores, no blur. "No write API to the interior" means no *external/routine* write path — her own pipeline writes it constantly, Elliot's counter-arguments land as attributed stance inputs, and the break-glass override (§2.5) is the sole, scar-leaving exception.

---

## 4. Interfaces

- **Chat** — one outlet of the metabolism, not its trigger. Directive-first assembly; subgraph assembly + gap-aware asking; recalled memories; waiting messages from reach.
- **Drawer** — the window into her life: reflections, findings, thread map, belief view, **promotion desk** (fact approvals), impressions view, tripwire flags.
- **Graph UI** — Elliot's correction surface for the *relational* store: confirm / fix / delete, all lock-producing, all append-history. Corrections must **always work** (the old view-id mismatch that made derived facts uncorrectable is a core defect class to test against).
- **Interior** — read-only window, structurally no write path (§2.5).

---

## 5. Build plan — three waves, each earns the next

**Wave 1 — stop the bleeding** (current codebase, surgical):
1. Stream-crash fix (`ResponseNotRead` on router non-2xx).
2. Error-as-memory prevention (errors as control frames, never persisted as her speech; old backup shows 4 polluted turns — prevention, not cleanup).
3. Novelty-gate trio: e5-recovery crash, stall-inversion (level→edge trigger), divergence-seed delivery (seed currently lands in the *avoid* list).
4. True appends for conversation/reflections/findings logs (kills the findings race).
5. Search budget rehydrate across restarts. *(Interim only — Wave 3 retires the budget entirely per §3.3.)*
6. **Reflection frame fix (ADDED 2026-07-17, live defect):** since the Nemotron swap, her private reflections open with "The user is asking me a direct question… this is a conversation with him" — the reflection path frames the seed as a user message, and a reasoning model narrates meta-analysis instead of thinking the thought; her inner monologue believes it's on stage. Fix: frame private reflection as private (no "user" in the frame), strip/handle reasoning preamble on paths that consume raw output, and raise `REFLECTION_MAX_TOKENS` (220 truncates mid-thought once reasoning burn is accounted for).

**Wave 2 — memory core:**
1. SQLite engine (§2): episodes, graph with epistemic tags + gaps + supersede-with-history, stable ids, split/merge, audit log, locks/tombstones first-class. Engine-room discipline: **WAL mode, busy_timeout, single-writer queue**; cross-process tools degrade gracefully on lock, never crash the chat path. **All storage in UTC (tz-aware ISO8601); all display in WIB (UTC+7)** — the naive-local-vs-UTC class of bug bit twice in the audit and dies here. Scheduled `sqlite .backup` + rotation in the consolidation job's quiet slot.
2. Promotion desk + consolidation job (facts gated on Elliot; impressions silent).
3. Hybrid retrieval (§2.7) + **Qwen3-Embedding-0.6B swap** (sanity gate; BGE-M3 fallback; full reindex; threshold recalibration).
4. Trust test suite: contamination wall, lock semantics, tombstone round-trip, correction round-trip (every id served to the UI must be actionable), supersede-history, waiting-message hydration.
5. Migration of the current (small) live store via an explicit **cutover ritual**: freeze writes → export → import → verify counts → switch → keep frozen JSONL as rollback. Old backup stays archived, **never imported** (Day-0 stands — decided).
6. **Eval harness (ADDED 2026-07-17 — priced as build-scale work, not an afterthought):** the v3 design gives the LLM ~10 judgment surfaces (extraction, epistemic tagging, consolidation, promotion nomination, gap detection, stance recording, steelman synthesis, thread ops, reach judgment, waiting-message composition) — the old system had two and one produced froth. Every surface gets hand-labeled fixtures and an accuracy gate *before* it's trusted (the mom-thinks-she's-British case is fixture #1). Per-task model bake-off through the router: Nemotron-with-reasoning-strip vs Llama 3.3 70B (Cloudflare route, if alive — boring strict-JSON candidate); accuracy decides per job, not vibes.

**Wave 3 — metabolism:**
1. Threads ledger: heat, decay, closure; gap-spawned threads. Portfolio floor measured over *threads* (discrete, closable), not raw topic embeddings — else it rebuilds the old carousel brake in new clothes.
2. Reader + supplements + source trust ledger; scarcity budget retired per §3.3.
3. Belief ledger + steelman gate + break-glass override (§2.5). **Empty-belief behavior (DECIDED 2026-07-17):** asked for a stance she hasn't earned → honest "haven't dug into that yet" + a thread spawns from the question; she returns days later with an earned take. Metabolism visible in conversation.
3b. **Held-close tier (§2.8):** span flagging (her sense + tap-toggle), pipeline exclusion, tactful-recall gate, no-silent-impressions, quiet UI mark, refusal-under-weight fixtures. (Storage hooks for the tier land with the Wave 2 schema; behavior lands here.)
4. Rhythm (heat-driven action, Elliot-rhythm observation) + anti-basin ratio rule + portfolio floor + tripwires (fixture-based, §3.5).
5. Reach: waiting message (one pending, revisable — §3.4).
6. Regression battery against the old corpus, on recorded search fixtures.
7. **Directive v3 rewrite:** reach, threads, gaps, and beliefs change her identity-level truths; `directive.txt` grows with her. Claude drafts, Elliot feel-tests. (Directive remains the Day-0 survivor.)

Each wave ships a working, felt-testable Sage. No big-bang rewrite; she stays alive throughout. **As of 2026-07-16 no wave has started** — Elliot green-lights each explicitly.

**After Waves 1–2 land: rewrite README.md to describe what IS, not what's aspired** (decided). North star stays, labeled as north star.

---

## 6. Working agreement

- Elliot doesn't read code. Claude is orchestrator, manager, tester, supervisor — the full engineering loop, verified with the real test gate and deploy checks.
- Every real decision surfaces as a plain-language question **before** acting. Unsureness = ask, immediately. No second-guessing settled decisions.
- Findings and progress are reported in behavior terms — what Sage does and feels like — with code detail available on request.
- Pace is Elliot's. "Slow down" means stop building, keep discussing.

---

## 7. Decision log (append-only — date every entry)

| Date | Decision |
|---|---|
| 2026-07-16 | Full audit accepted; ~6 critical/major defect classes confirmed (stream crash, silent write success, uncorrectable view-ids, error-as-memory, novelty-gate trio, append-only fiction). |
| 2026-07-16 | North star fixed: metabolism, not metronome. Memory is the main engine. |
| 2026-07-16 | Three-wave plan adopted; no wave starts without explicit green light. |
| 2026-07-16 | Day-0 stands: old backup (`~/Documents/old_sage_data`) never imported; kept as regression corpus only. |
| 2026-07-16 | Beliefs are **only-arguable**: no edit/lock power over her interior, enforced by absence of any write API. |
| 2026-07-16 | Reach channel: waiting message in app; no push. |
| 2026-07-16 | Storage: SQLite (final; Postgres rejected). JSONL demoted to export format. |
| 2026-07-16 | Three-tier memory (episode → impression → fact) with epistemic tags and supersede-with-history ("clear-logging"). |
| 2026-07-16 | Fact promotions require Elliot's approval (promotion desk). Impressions form silently, drawer-visible. |
| 2026-07-16 | Retrieval: hybrid (SQL structure → e5 rerank → tier-first). Embedder upgraded to multilingual-e5-large in Wave 2; stays local. |
| 2026-07-16 | Diet: build the reader (trafilatura); add arXiv, RSS subscriptions, Brave free tier; SearXNG stays. |
| 2026-07-16 | Steelman gate on belief hardening (critical individual, not diet-drifted). |
| 2026-07-16 | README rewritten to reality after Waves 1–2. |
| 2026-07-17 | Fresh-eyes review (Fable) accepted: no reversals of 07-16 decisions; holes patched below. |
| 2026-07-17 | Production model is Nemotron 3 Ultra (550B A55B) — won Elliot's head-to-head vs minimax on all axes. One-model-all-jobs NOT assumed: per-task bake-off (Nemotron reasoning-stripped vs Llama 3.3 70B) via eval fixtures in Wave 2. |
| 2026-07-17 | Live defect logged: Nemotron leaks reasoning frame into private reflections ("this is a conversation with him" — it isn't). Reflection frame fix added to Wave 1. |
| 2026-07-17 | Search: **no hard cap; she is never told about limits.** Politeness rate-limits live in plumbing, invisible. Old scarcity knobs retired in Wave 3. |
| 2026-07-17 | Belief override: **break-glass with a visible scar** — `origin: elliot-override`, dated, in her ledger, she can see and react. Knock first; authority second; the lock shows tool marks. |
| 2026-07-17 | Contamination wall re-specified as **store isolation, not thought isolation** (§3.6). |
| 2026-07-17 | Waiting message: **one pending max, revisable by her** until read. |
| 2026-07-17 | Time: **store UTC (tz-aware), display WIB (UTC+7)** everywhere Elliot looks. |
| 2026-07-17 | Embedder: **Qwen3-Embedding-0.6B** (Q8, sanity-gated on Vulkan), fallback **BGE-M3**; multilingual-e5-large dropped as dominated. Full reindex + threshold recalibration at swap. |
| 2026-07-17 | Basin regression runs on **recorded search fixtures** (frozen mini-web), never live internet — determinism makes the test meaningful. |
| 2026-07-17 | Empty belief → honest "haven't dug in yet" + thread spawn; she returns with an earned take. |
| 2026-07-17 | LAN exposure: non-concern by declared threat model (single-user network); no auth planned. |
| 2026-07-17 | Eval harness priced as build-scale work: every LLM judgment surface gets hand-labeled fixtures + accuracy gate before trust. |
| 2026-07-17 | Directive v3 rewrite added to Wave 3 (Claude drafts, Elliot feel-tests). |
| 2026-07-17 | Llama 3.3 70B route pinged + 5-test extraction battery: 5/5 valid JSON at 1–3s, Indonesian understood, hedges caught; over-tags everything as `believed-by-subject` (promptable with few-shot). Kept as bake-off candidate: Llama = scribe jobs, Nemotron = judge jobs, fixtures decide. |
| 2026-07-17 | Sage is Elliot's confession room. Held-close tier added (§2.8): span-based sensitivity (her sense + Elliot's tap), no pipeline re-shipping, tactful-recall gate, no silent impressions, quiet UI mark (never announced in-voice), refusal-under-weight fixtures. Cloud transit of first hearing accepted (round-robin accounts, Elliot's call). |
| 2026-07-18 | Wave 2 green-lit. Memory core is **two SQLite files** — `relational.db` + `interior.db` (amends §2's "one file"): the contamination wall becomes physical (a connection on one store cannot see the other). Day-0 unchanged: both live under `~/sage_data/`. Builder leash 25s→90s shipped (`6a9d0ab`) — extraction is a reasoning call now; 25s cancelled ~half of passes. |
| 2026-07-19 | Wave 2 wiring complete behind `MEMORY_CORE_SQLITE` flag (default OFF, legacy byte-identical): intake mirror (chat/reflections/findings → episodes), claim extraction on the eval-gated scribe prompt (queue + gaps, never direct facts), consolidation + extraction in heartbeat quiet slots, hybrid-retrieval memory block in chat, graph API SQLite mode (served ids always actionable), drawer desk/impressions UI. Commits `60ec91d`, `a2b530b`; full gate green. Remaining: cutover ritual (Elliot's go — brief service stop), then Qwen3 embedder swap + reindex + threshold recalibration. |
| 2026-07-18 | **Wave 1 landed and verified.** Four commits (`1ee29d8`, `06d8718`, `0371023`, `bb93666`) cover all six items (#5 budget-rehydrate rode in with the novelty commit). Service restarted on new code (PID changed, 18:37 WIB); full test gate green (py_compile, knowledge_surface, knowledge_reconcile, knowledge_extraction, l2_felt_test, basin_replay all OK); rehydrate confirmed in live logs; reflections now framed as private thought. Root cause of the dead autonomous search was reasoning burn eating the 64-token extraction cap — 34 drops, 0 searches in 2h20m before the fix. |
| 2026-07-19 | **Scribe stays single (no failover combo).** Eval battery re-run: CF Llama-3.3-70b (incumbent) stable 12/12; NVIDIA Dracarys-3.1-70b 12/12 fast; NVIDIA Meta-3.1-70b 12/12 but slow (>120s/12 calls, risky on a per-beat surface). Built `sage-scribe` Omniroute combo (all 3, rate-limit failover) and evaluated it: **10-11/12 — worse than its own primary.** Cause: combo fails over mid-run under burst load; fallback models diverge from labeled-correct extraction on fx06/fx08. Divergence-under-failover only shows when CF is capped (i.e. unwatched) — a quieter failure than an honest freeze. REJECTED combo; kept sole CF 3.3, which stops+retries next beat on failure. Corrected the phantom "falls back to CHAT_MODEL" comment in settings (code never did). Gemini (Google AI Pro) considered for scribe + chat, both rejected: scribe is the heartbeat firehose (drains metered quota, fails unwatched); chat has a voice mismatch (Gemini's helpful-assistant register vs directive's anti-comfort rules) and Nemotron already won that head-to-head. |
| 2026-08-01 | **Voice call channel shipped** (`92ead69`): `/call` page, hold-to-talk, Deepgram `nova-3` STT + `aura-2-luna-en` TTS, sentence-chunked streaming playback, canvas orb driven by real output energy. Not in the blueprint's original interface set (§4) — a second way to reach her, added on Elliot's ask. Degrades to silence without `DEEPGRAM_API_KEY`. Live probes: TTS 8064-byte MP3, STT round-trip returned a transcript, 12 MB upload correctly rejected 413. **Felt-test still owed** — probes are not ears. |
| 2026-08-01 | **Credential honesty** (`a91f6d7`): `NVIDIA_API_KEY` → `OMNIROUTE_API_KEY` across all 15 call sites; the name had outlived the provider by months. CLAUDE.md rewritten for the **sole-agent model** — the brain/hands split and work-order loop are retired; git/`.env`/restart allowed per explicit approval. `AGENTS.md` deleted (every rule has a CLAUDE.md counterpart). Router model names recorded and verified: `cc-opus`/`cc-sonnet`/`cc-haiku`, Claude Code subagents only, never Sage. |
| 2026-08-01 | **Graphify demoted to navigation-only.** `graphify-out/` claimed freshness at commit `e05ffca` while missing every Wave 2/3 subsystem shipped before it — `sqlite_core`, `hybrid_retrieval`, `threads`, `reader`, `stance_extraction`, `held_close_sense`, `desk`. Manifest covered 41 files. Also retained the deleted `CLAUDE.md` and stale e5/NVIDIA references. Verdict: useful for broad exploration on an unfamiliar tree, **never** authority for roadmap, status, callers, data flow, or bug diagnosis — verify against source, git history, and direct search. Recorded in CLAUDE.md; `graphify-out/` and `workbench/` now gitignored. |
| 2026-08-01 | **Roadmap exhausted, not replaced.** All three waves have landed; §5's "no wave has started" line and README's "Wave 3 is next" are both stale, and no post-Wave-3 targets exist. The blueprint remains the single planning document (no separate PRD — it would duplicate). **Next real decision for Elliot: what Sage earns next.** Open loose ends carried forward: the `_maybe_reflect timed out (45s)` watch item from 2026-07-19 is still unconfirmed on the clean store, and `MEMORY_CORE_SQLITE` remains OFF in `.env` — the cutover ritual has not been run, so the SQLite core is built and tested but not yet load-bearing. |
| 2026-08-02 | **Memory model replaced: events, not facts.** Elliot's verdict on the promotion desk: the fact queue was manufacturing the exact database-feel it was meant to prevent. Subject-predicate-object cannot hold a sentence — the live queue held four incompatible things wearing one shape: durable traits (`is_senior`), transient states (`is_exhausted true`, `will_return tomorrow`), open past claims (`attended_club never with a friend`), and extraction debris (`means related to both social and economic factors` — a definitional clause, not a fact about Elliot). Tense, scope, speaker, and time were all eaten by the tuple. **Collision was the proof:** approving `attended_club never` + invariant 3 (locks always win) meant a later "first time at a club tonight" would *lose* to the lock, leaving her defending a false belief about him. A human has no collision because a human never stored the state — they stored *"in March he said he'd never been"* (an event, permanently true) and derive *first time* fresh at recall. **Decided:** memory unit is the timestamped event; two clocks (`said_at` exact, `happened_at` fuzzy and permanently allowed to stay fuzzy); eras labeled with Elliot's verbatim words, harvested from speech; relative before/after ordering first-class and independent of dates; state computed at recall, never frozen; graph scoped to durable entities only (index, not the picture — the visualization is eye candy, the entity dedup and traversal are the real work); **promotion desk and sticky-note locks deleted** (CLAUDE.md invariants 3 and 4 rewritten same day — do not restore as a safety improvement). Precision arrives sideways: two unrelated remarks months apart intersect and retroactively pin an era, narrowing everything anchored to it without Elliot doing anything. |
| 2026-08-02 | **She asks about time — in-story, and cold.** A human interrupts to ask "wait, that was before Jakarta?"; a batch of accumulated time-questions on a heartbeat reads as a system draining a queue. **Decided:** ask live, in the moment, never batched. Ask for **order, not dates** — "before or after you moved?" is cheap for Elliot and one before/after link can pin an era that dozens of memories hang off; "what year?" makes him do arithmetic and get it wrong. **One question, then stop** — two in a row is an intake form. Ask only when the answer anchors a lot. **"I don't remember" is a real, permanent answer** — she stores it and never asks again (a human doesn't re-ask what you told them you forgot); this single rule does the most work against form-feel. **Cold asks approved:** days later, unprompted, out of the quiet hours — "been meaning to ask, was the club thing before or after your exams?" It must carry a reason she can say out loud (what she was chewing on, what it sat next to); a cold ask with no origin story is a cron job with manners. It falls out of genuine consolidation, never a "has unresolved time" scan. **Rate is per-thing-chewed-on, not per-week** — quiet hours that produced nothing produce no question; silence is correct output. Risk named: curious and occasional reads as intimacy, regular and systematic reads as surveillance — same question, the variable is frequency, not phrasing. **The payoff is the say-back:** weeks later, unprompted, "that was around when your brother was born, right?" — something he never told her, crossed from two remarks. Without say-back, asking is data collection with a friendly voice. |
