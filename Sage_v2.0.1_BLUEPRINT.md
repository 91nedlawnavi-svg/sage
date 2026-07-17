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

1. **Free-tier models only.** All inference through local Omniroute router `localhost:20128`, model alias `sage`. Never the paid Claude API. (Current model: minimax-m3, won the voice battery.)
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
- **Only-arguable (DECIDED, philosophical line):** Elliot has **no edit or lock power** over her beliefs — argue in conversation only. Losing an argument honestly = his counter-evidence lands as a stance event and the belief shifts on merit; it must neither fold because he pushed (yes-man) nor dig in to perform spine (edgelord). Enforced structurally: the interior store has **no write API**. None. The wall protects her mind from him exactly as locks protect his facts from her froth.

### 2.6 Source trust ledger

She records which domains/sources gave substance vs slop, which burned her (claims that didn't hold up). Learned distrust, hers, with receipts. Feeds search ranking and belief weighting.

### 2.7 Retrieval — hybrid, structure first

Flat cosine-scan over years of episodes degrades (everything about "work" looks alike). The fix is order, not a bigger embedder:

1. **SQL structural index first** — entity links, dates, tiers. "Job question" → all job-linked memories, exact and complete, milliseconds.
2. **e5 reranks within** the candidate set — similarity works well on small sets.
3. **Tier-first** — she usually retrieves distilled impressions/facts; raw episodes only when needed ("when did I say that?").
4. Recency/frequency weighting on top.

**Embedder (DECIDED): upgrade to multilingual-e5-large now, in Wave 2.** Same dim (1024), same server pattern, ~1.3 GB quantized — fits the 3 GB headroom. Motive: Elliot writes to her in Indonesian; current e5-large-v2 is English-mainly. Local stays non-negotiable.

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

### 3.4 Reach — she opens the conversation

**Channel (DECIDED): waiting message in app.** A thread crosses an importance threshold → she writes to Elliot; her message is already sitting in chat when he next opens the UI, like a text sent while he was away. No push notifications. This delivers directive line 44 ("you're allowed to reach first"), which currently no code delivers.

### 3.5 Anti-basin — organism-level, not a bolt-on brake

Root cause of the mindfulness collapse: **self-cannibalism** — her reflections fed on her own recent reflections; an LLM echoes its context; the spiral tightened.

1. **Input/output ratio rule (the big one):** her context for reflection is mostly fresh external material + *digests* of her recent thought — never raw recent reflections. She thinks *about things*, not about her thoughts about her thoughts.
2. **Closure** (§3.1) — threads carry their own exit.
3. **Portfolio floor:** heat decays; no thread eats more than a bounded share of weekly attention; monopoly triggers forced breadth (a real diversification, not the old bugged seed carousel).
4. **Tripwires:** the old corpus is the regression test — new mind run in timelapse must never reproduce the 2,487/4,487 concentration; plus a live topic-concentration flag in the drawer so Elliot sees relapse early.

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
5. Search budget rehydrate across restarts.

**Wave 2 — memory core:**
1. SQLite engine (§2): episodes, graph with epistemic tags + gaps + supersede-with-history, stable ids, split/merge, audit log, locks/tombstones first-class.
2. Promotion desk + consolidation job (facts gated on Elliot; impressions silent).
3. Hybrid retrieval (§2.7) + multilingual-e5-large swap.
4. Trust test suite: contamination wall, lock semantics, tombstone round-trip, correction round-trip (every id served to the UI must be actionable), supersede-history.
5. Migration of the current (small) live store; old backup stays archived, **never imported** (Day-0 stands — decided).

**Wave 3 — metabolism:**
1. Threads ledger: heat, decay, closure; gap-spawned threads.
2. Reader + supplements + source trust ledger.
3. Belief ledger + steelman gate.
4. Rhythm (heat-driven action, Elliot-rhythm observation) + anti-basin ratio rule + portfolio floor + tripwires.
5. Reach: waiting message.
6. Regression battery against the old corpus.

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
