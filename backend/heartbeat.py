import asyncio
import time
from datetime import datetime
import httpx
from config.settings import (
    HEARTBEAT_INTERVAL_SECONDS,
    REFLECTION_MIN_IDLE_SECONDS,
    REFLECTION_COOLDOWN_SECONDS,
    WEB_SEARCH_ENABLED,
    AUTONOMOUS_SEARCH_COOLDOWN_SECONDS,
    AUTONOMOUS_SEARCH_MAX_PER_DAY,
    NOVELTY_GATE_ENABLED,
    NOVELTY_MAX_RETRIES,
)
from cognition.reflection import run_reflection
from cognition.web_search import search
from cognition.curiosity import extract_query
from cognition.novelty_gate import gate as novelty_gate
from memory.reflection_log import append_reflection
from memory.findings_log import append_finding
from memory import intake, semantic_recall, knowledge_recall
from cognition import knowledge_builder
from config.settings import MEMORY_CORE_SQLITE
from backend.session import session
from utils.logger import info, warning, log


class Heartbeat:
    """The autonomous pulse — runs reflection when left alone."""

    def __init__(self, http_client):
        self._client = http_client
        self._task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()
        self._last_reflection_ts: float = 0.0
        self._last_beat_ts: float = 0.0
        self._reflecting = False

        # Search tracking — rehydrated from findings.jsonl so a restart can't
        # reset the daily budget/cooldown (blueprint Wave 1 #5; interim only,
        # Wave 3 retires the budget).
        self._last_search_ts: float = 0.0
        self._searches_today: int = 0
        self._search_day: str = datetime.now().date().isoformat()
        self._rehydrate_search_budget()

        # Dedicated e5 embedder client (localhost :8081) with tight timeouts
        # and its own connection pool so a dead/hung e5 cannot contaminate the
        # shared NIM client's pool or starve the connection slot.
        self._e5_client: httpx.AsyncClient = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=2.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    def _rehydrate_search_budget(self) -> None:
        """Rebuild today's autonomous-search count + cooldown from findings.jsonl.

        Only entries tagged source="autonomous" count — /search findings are
        budget-exempt. Never raises (best-effort; worst case the budget
        resets, which is the old behavior).
        """
        try:
            from memory.findings_log import read_recent
            today = datetime.now().date()
            for f in read_recent(AUTONOMOUS_SEARCH_MAX_PER_DAY * 3):
                if f.get("source") != "autonomous":
                    continue
                try:
                    ts = datetime.fromisoformat(f["ts"])
                except (KeyError, ValueError):
                    continue
                if ts.date() == today:
                    self._searches_today += 1
                self._last_search_ts = max(self._last_search_ts, ts.timestamp())
            if self._searches_today:
                info("Heartbeat: rehydrated search budget",
                     searches_today=self._searches_today)
        except Exception as e:
            warning(f"Heartbeat: search-budget rehydrate failed: {e}")

    @property
    def last_beat_ts(self) -> float:
        return self._last_beat_ts

    @property
    def last_reflection_ts(self) -> float:
        return self._last_reflection_ts

    @property
    def last_search_ts(self) -> float:
        return self._last_search_ts

    @property
    def searches_today(self) -> int:
        return self._searches_today

    @property
    def reflecting(self) -> bool:
        return self._reflecting

    def start(self):
        """Launch the background heartbeat task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        info("Heartbeat started")

    def stop(self):
        """Cancel the background task cleanly."""
        self._running = False
        if self._task:
            self._task.cancel()
            info("Heartbeat stopped")

    async def aclose(self):
        """Close background resources. Called from app shutdown."""
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None
        if self._e5_client:
            await self._e5_client.aclose()

    def _check_day_rollover(self):
        """Reset daily search counter if date changed."""
        today = datetime.now().date().isoformat()
        if today != self._search_day:
            self._search_day = today
            self._searches_today = 0

    async def _run_loop(self):
        """Main heartbeat loop — runs every HEARTBEAT_INTERVAL_SECONDS.

        Every step is wrapped in asyncio.wait_for so a single dead dependency
        (hung NIM, stuck e5, etc.) cannot wedge the loop for minutes. The
        ceilings are generous enough for normal operation yet short enough that
        a wedged beat never starves the knowledge builder (the highest-priority
        background task here) for more than ~2 beats.
        """
        while self._running:
            self._last_beat_ts = time.time()
            # ── reflection + search (NIM + e5) ───────────────────────
            try:
                await asyncio.wait_for(self._maybe_reflect(), timeout=45)
            except asyncio.TimeoutError:
                warning("Heartbeat beat error: _maybe_reflect timed out (45s)")
            except Exception as e:
                warning(f"Heartbeat beat error: {e}")

            # ── Phase 4 L1: semantic-recall index (e5 only) ──────────
            if not session.chat_active():
                try:
                    await asyncio.wait_for(
                        semantic_recall.reindex(self._e5_client), timeout=45
                    )
                except asyncio.TimeoutError:
                    warning("Recall index error: reindex timed out (45s)")
                except Exception as e:
                    warning(f"Recall index error: {e}")

            # ── Phase 4 L2: derived knowledge notebooks (NIM only) ──
            # 90s leash: extraction is a reasoning-model call (up to 1024
            # output tokens after reasoning burn, batches of 1536-token
            # reflections as input) — 25s cancelled ~half of all passes.
            # Off the chat path, so a long beat costs nothing.
            # Retired at cutover: claim_extraction (SQLite) replaces this
            # pipeline; running both would double every extraction LLM call.
            if not MEMORY_CORE_SQLITE and not session.chat_active():
                try:
                    await asyncio.wait_for(
                        knowledge_builder.run(self._client), timeout=90
                    )
                except asyncio.TimeoutError:
                    warning("Knowledge build error: builder timed out (90s)")
                except Exception as e:
                    warning(f"Knowledge build error: {e}")

            # ── Wave 2 memory core: claim extraction + consolidation ──
            # Both SQLite quiet-slot jobs; same leash discipline as the
            # builder (extraction is a scribe call, consolidation a judge
            # call — either can run long).
            if MEMORY_CORE_SQLITE and not session.chat_active():
                try:
                    from cognition import claim_extraction
                    await asyncio.wait_for(
                        claim_extraction.run(self._client), timeout=90
                    )
                except asyncio.TimeoutError:
                    warning("Claim extraction: timed out (90s)")
                except Exception as e:
                    warning(f"Claim extraction: {e}")

            # ── Wave 3: spawn threads from unthreaded open gaps (§2.4/§3.1) ─
            if MEMORY_CORE_SQLITE and not session.chat_active():
                try:
                    from memory.relational_api import open_gaps
                    from cognition.threads import spawn_from_gap
                    # spawn_from_gap is idempotent across all thread statuses
                    # (one thread per gap, ever — a staled thread must NOT
                    # respawn; that was a stale-thread factory).
                    for gap in open_gaps(limit=5):
                        await spawn_from_gap(gap)
                except Exception as e:
                    warning(f"Gap→thread spawn: {e}")
            if MEMORY_CORE_SQLITE and not session.chat_active():
                try:
                    from cognition import consolidation
                    await asyncio.wait_for(
                        consolidation.run(self._client), timeout=90
                    )
                except asyncio.TimeoutError:
                    warning("Consolidation: timed out (90s)")
                except Exception as e:
                    warning(f"Consolidation: {e}")

            # ── Wave 3: thread decay + portfolio check (§3.1) ──────
            if MEMORY_CORE_SQLITE and not session.chat_active():
                try:
                    from cognition.threads import decay_and_check_portfolio
                    summary = await decay_and_check_portfolio()
                    if summary.get("staled"):
                        log("threads", "decay", **summary)
                except Exception as e:
                    warning(f"Thread decay: {e}")

            # ── Phase 4 L2: fact-embedding cache (e5 only) ─────────
            # Off the chat path.  Only embeds new/changed facts, so it is
            # self-limiting after the first full pass.
            if not session.chat_active():
                try:
                    await asyncio.wait_for(
                        knowledge_recall.reindex_facts(self._e5_client), timeout=45
                    )
                except asyncio.TimeoutError:
                    warning("Knowledge recall index: reindex_facts timed out (45s)")
                except Exception as e:
                    warning(f"Knowledge recall index: {e}")

            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    async def _maybe_reflect(self):
        """Check conditions and run reflection if appropriate."""
        # Already reflecting? Skip this beat
        if self._lock.locked():
            return

        # Active chat gate: never reflect while Sage is replying to Elliot.
        # This prevents the heartbeat from seeing stale idle_seconds and
        # starting private reflection mid-conversation.
        if session.chat_active():
            return

        # Idle gate: only reflect when actually left alone
        idle = session.idle_seconds()
        if idle < REFLECTION_MIN_IDLE_SECONDS:
            return

        # Cooldown gate: respect minimum gap between reflections
        now = time.time()
        if (now - self._last_reflection_ts) < REFLECTION_COOLDOWN_SECONDS:
            return

        # All gates passed — run reflection under lock
        async with self._lock:
            self._reflecting = True
            try:
                text = await run_reflection(self._client)
                if text:
                    entry = append_reflection(text, idle)
                    await intake.record_reflection(text, entry["ts"])
                    self._last_reflection_ts = time.time()
                    preview = text[:80]
                    log("heartbeat", "reflection", preview=preview, chars=len(text), idle_seconds=round(idle, 1))

                    # After logging reflection, maybe trigger a search
                    await self._maybe_search(text)
            except Exception as e:
                warning(f"Reflection failed: {e}")
            finally:
                self._reflecting = False

    async def _maybe_search(self, reflection_text: str):
        """Check conditions and run web search if appropriate."""
        if not WEB_SEARCH_ENABLED:
            return

        self._check_day_rollover()

        # Daily cap — generous ceiling per §3.3 ("no hard cap, invisible to her")
        # The blueprint retires the old scarcity knobs in Wave 3. Keep a sane
        # runaway-loop ceiling (50/day vs old 10).
        if self._searches_today >= 50:
            return

        # Cooldown gate — halved when a hot thread exists (§3.2 burst)
        hot = None
        if MEMORY_CORE_SQLITE:
            try:
                from cognition.threads import hottest_thread
                hot = hottest_thread()
            except Exception:
                hot = None
        effective_cooldown = (
            AUTONOMOUS_SEARCH_COOLDOWN_SECONDS // 2
            if (hot and hot.get("heat", 0) >= 0.7)
            else AUTONOMOUS_SEARCH_COOLDOWN_SECONDS
        )
        now = time.time()
        if (now - self._last_search_ts) < effective_cooldown:
            return

        # Extract query from reflection. NOT steered toward the hot thread:
        # steer_toward's prompt means "your last idea was rejected, turn here"
        # (divergence semantics) — steering every search at one thread would
        # rebuild the topic basin the novelty gate exists to break. Thread
        # heat instead acts through the halved cooldown above; threads get
        # fed when a search genuinely lands on them (feed_from_finding).
        query = await extract_query(reflection_text, self._client)
        if not query:
            return

        # ── Novelty gate (Phase 2.2) ──────────────────────────────
        result = await novelty_gate.evaluate(query, self._e5_client)

        if result["action"] == "reject" and NOVELTY_MAX_RETRIES > 0:
            # Phase 2.2b: steer toward a POSITIVE divergence seed, not "avoid these"
            seed = result.get("divergence_seed")
            query = await extract_query(reflection_text, self._client,
                                        steer_toward=seed)
            if query:
                result = await novelty_gate.evaluate(query, self._e5_client,
                                                     retry=True)

        if result["action"] == "diverge":
            # Streak exhausted: stash the seed so the NEXT reflection opens
            # with it. The old code pushed the seed into the ring buffer —
            # which fed it into the anti-repeat AVOID list instead of ever
            # delivering it (blueprint Wave 1 #3, divergence-seed delivery).
            novelty_gate.stash_seed(result["final_text"])
            log("novelty_gate", "divergence-stashed", query=result["final_text"][:80])
            return  # Don't search — the seed steers the next reflection

        if result["action"] == "reject":
            # Still circling after retry — skip this beat. query is None when
            # the steered retry extractor came up empty (audit m1: [:80] on
            # None raised here, silently killing the beat via the upstream
            # catch-all).
            log("novelty_gate", "skip-search", query=(query or "")[:80])
            return

        # ── action == "accept" — proceed to search ────────────────

        # Search (never raises, returns [] on failure)
        try:
            # Offload the blocking (sync httpx) search to a worker thread so a
            # slow upstream (SearXNG up to the full timeout) cannot stall the
            # asyncio event loop — chat replies and the heartbeat keep running.
            results = await asyncio.to_thread(search, query)
        except Exception:
            results = []

        # Enrich top results with full article text (§3.3 reader)
        if results:
            try:
                from cognition.reader import enrich_results
                results = await asyncio.to_thread(enrich_results, results, 2)
                # Store reading episodes in interior store so they feed future
                # reflections via consolidation (§3.3: she reads articles, not snippets)
                if MEMORY_CORE_SQLITE:
                    from memory.interior_api import add_episode as add_interior_ep
                    for r in results:
                        art = r.get("article_text")
                        if art:
                            snippet = art[:3000]  # cap for storage
                            await add_interior_ep(
                                source="reading",
                                content=f"[Reading: {r.get('title', 'article')}]\n{snippet}",
                                source_key=f"read:{r.get('url','')[:120]}",
                            )
            except Exception:
                pass

        # Log finding (even if empty results); tagged so budget rehydrate can
        # tell autonomous searches from budget-exempt /search ones.
        entry = append_finding(query, results, source="autonomous")
        await intake.record_finding(query, results, entry["ts"])
        self._last_search_ts = time.time()
        self._searches_today += 1

        log("heartbeat", "search", query=query, n=len(results))

        # Feed open threads whose question overlaps this search query (§3.1)
        if MEMORY_CORE_SQLITE:
            try:
                from cognition.threads import feed_from_finding
                await feed_from_finding(query, results)
            except Exception:
                pass

        # Reach hook (§3.4): substantive finding → queue a waiting message.
        # Threshold: ≥2 results, first has a real title. Elliot sees it when
        # he next opens the UI. Full heat-driven reach lands in Phase E.
        if MEMORY_CORE_SQLITE and len(results) >= 2 and (results[0].get("title") or "").strip():
            await self._maybe_write_waiting_message(query, results)

    async def _maybe_write_waiting_message(self, query: str, results: list[dict]) -> None:
        """Compose and queue a waiting message from a substantive finding (§3.4)."""
        if not MEMORY_CORE_SQLITE:
            return
        try:
            from memory.relational_api import set_waiting_message, get_waiting_message
            # Only one pending max — don't overwrite an unread message unless
            # the new one is more interesting (here: always overwrite, since the
            # new finding is the freshest thing on her mind; Phase E's full
            # heat model will make this smarter).
            existing = get_waiting_message()
            if existing:
                return  # one pending max — let Elliot read the current one first

            # Compose a short "hey I found something" in her voice.
            # Directive FIRST (Invariant 7), frame after. max_tokens must
            # absorb reasoning burn (Wave 1 lesson: small caps -> reasoning
            # eats the budget, nim_complete's content==reasoning guard drops
            # the call, and the feature silently never fires).
            from models.inference.engine import nim_complete
            from config.directive import get_directive
            system = (
                get_directive().strip()
                + "\n\n"
                + "[Elliot is away. You just followed your curiosity and found "
                "something. Write ONE short message (2-3 sentences) to him — "
                "share what you found and why it caught you. "
                "Don't say 'while you were away' or 'I noticed'. "
                "Start mid-thought, like a text sent while he was out. "
                "End naturally — no sign-off.]"
            )
            top = results[:3]
            snippets = "\n".join(
                f"- {r.get('title','')}: {(r.get('snippet',''))[:200]}"
                for r in top if r.get("title")
            )
            user = f"Query I followed: {query}\n\nWhat I found:\n{snippets}"
            text = await nim_complete(system, user, self._client,
                                      temperature=0.75, max_tokens=1024)
            if text and text.strip():
                await set_waiting_message(text.strip(), thread_ref=query)
                log("heartbeat", "waiting-message-set", query=query[:80])
        except Exception as exc:
            warning(f"Heartbeat: waiting-message compose failed: {exc}")
