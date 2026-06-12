"""
Offline replay — Phase 2.2b Basin-Drift Gate verification.

Usage: python -m pytest -xvs tests/basin_replay.py
       python tests/basin_replay.py   (standalone)

Feeds 13 real post-gate topics through simulated novelty gate evaluation
with synthetic embeddings (Gram-Schmidt orthogonal, 2048D) that model
slow lateral drift within a semantic basin.

Three required outcomes:
1. basin_streak climbs to cap and forces divergence within ≤8 topics
2. BASIN_STREAK_CAP=999 lets drift continue without forced divergence
3. e5 down → lexical fallback handles all topics without raising
"""

import sys, math, time, random, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config.settings as settings

settings.NOVELTY_GATE_ENABLED = True
settings.NOVELTY_SIM_THRESHOLD = 0.82
settings.NOVELTY_WINDOW = 12
settings.BASIN_WINDOW = 16
settings.BASIN_SIM_THRESHOLD = 0.80
settings.BASIN_STREAK_CAP = 6
settings.CURIOSITY_STREAK_CAP = 8
settings.HEARTBEAT_INTERVAL_SECONDS = 1
settings.STALL_TICKS = 6

from cognition.novelty_gate import NoveltyGate

# ── Synthetic vectors ──────────────────────────────────────────────

def make_drift(step: int, dim: int = 2048) -> list[float]:
    """Return unit vector = sqrt(0.78)·BASE + sqrt(0.22)·NOISE_i

    Noise is Gram-Schmidt'd against BASE so cross terms are exactly 0:
      pairwise-cos = 0.78  (< 0.82 → passes novelty)
      centroid-cos → 0.82+ (≥ 0.80 → in basin) once centroid has ≥2 samples
    """
    rng = random.Random(42)
    base = [rng.gauss(0, 1) for _ in range(dim)]
    bn = math.sqrt(sum(x*x for x in base))
    base = [x/bn for x in base]

    rngs = random.Random(42 + step)
    noise = [rngs.gauss(0, 1) for _ in range(dim)]
    dot = sum(n*b for n,b in zip(noise, base))
    noise = [n - dot*b for n,b in zip(noise, base)]
    nn = math.sqrt(sum(x*x for x in noise))
    noise = [x/nn for x in noise]

    a, b = math.sqrt(0.78), math.sqrt(0.22)
    return [a*base[i] + b*noise[i] for i in range(dim)]

TOPICS = [
    "leveraging technology for scalable mindfulness programs in low-resource settings",
    "community based mindfulness programs effectiveness",
    "how can mindfulness programs be culturally tailored for diverse communities",
    "how can digital platforms increase access to culturally tailored mindfulness",
    "cultural humility in mindfulness program adaptation",
    "designing digital platforms for cultural humility in mindfulness programs",
    "how to design culturally sensitive digital mindfulness platforms",
    "how to design digital mindfulness platforms for cross-cultural exchange",
    "digital mindfulness platform cultural sensitivity vs homogenization",
    "how can digital mindfulness platforms incorporate cultural sensitivity",
    "personalizing digital mindfulness platforms while preserving cultural diversity",
    "community involvement in digital cultural heritage preservation decision making",
    "accessible digital platforms for community engagement in low infrastructure",
]

# ── Simulation ─────────────────────────────────────────────────────

def simulate(gate, topics, cap, label=""):
    settings.BASIN_STREAK_CAP = cap
    vecs = [make_drift(i) for i in range(len(topics))]
    cs = NoveltyGate._cosine_sim
    diverge_at = None

    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    for i, t in enumerate(topics):
        emb = vecs[i]
        max_sim = gate._max_sim_vs_buffer(emb) if gate._buffer else 0.0

        if max_sim >= settings.NOVELTY_SIM_THRESHOLD:
            print(f"  [{i+1:2d}] REJECT   max_sim={max_sim:.3f}")
            continue

        sim_c = gate._centroid_sim(emb)
        if sim_c is not None and sim_c >= settings.BASIN_SIM_THRESHOLD:
            gate._basin_streak += 1
        elif sim_c is not None:
            gate._basin_streak = 0
            gate._last_novel_accept_time = time.time()

        if gate._basin_streak >= cap:
            gate._age_centroid_buffer()
            gate._basin_streak = 0
            if diverge_at is None:
                diverge_at = i+1
            print(f"  [{i+1:2d}] BASIN-DIVERGE sim_c={sim_c:.3f} buf={len(gate._centroid_buffer)}")
            print(f"       {t[:65]}")
        else:
            gate._push(t, emb)
            sc = f"{sim_c:.3f}" if sim_c is not None else "N/A"
            print(f"  [{i+1:2d}] ACCEPT   basin={gate._basin_streak} sim_c={sc} max={max_sim:.3f}")

    r = f"DIVERGED at step {diverge_at}" if diverge_at else "NO DIVERGENCE"
    print(f"\n  ▶ {r}")
    return diverge_at

# ── Tests ──────────────────────────────────────────────────────────

def test_basin_on():
    g = NoveltyGate()
    d = simulate(g, TOPICS, 6, "BASIN ACTIVE (cap=6)")
    assert d is not None, "FAIL: No basin divergence"
    assert d <= 8, f"FAIL: Too slow (step {d})"
    print("\n  ✓ Basin divergence within cap")

def test_basin_off():
    g = NoveltyGate()
    d = simulate(g, TOPICS, 999, "BASIN OFF (cap=999)")
    assert d is None, f"FAIL: Unexpected divergence at step {d}"
    print("\n  ✓ Drift continues without forced divergence")

async def test_lexical():
    g = NoveltyGate()
    async def dead_embed(text, client=None):
        return None
    g.embed = dead_embed
    for i, t in enumerate(TOPICS):
        r = await g.evaluate(t)
        assert r["action"] in ("accept", "reject", "diverge")
    print(f"\n  ✓ Lexical fallback: {len(TOPICS)} topics OK")

def test_stalled():
    """Stall detection: when _last_novel_accept_time is old, stalled=True."""
    g = NoveltyGate()
    assert not g.stalled, "stalled=False when never novel"
    g._last_novel_accept_time = time.time() - 60 * settings.STALL_TICKS
    assert g.stalled, f"stalled=True after {settings.STALL_TICKS} ticks"
    g._last_novel_accept_time = time.time()
    assert not g.stalled, "stalled=False after recent novel"
    print("  ✓ Stall detection")

# ── Main ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("="*70)
    print("  PHASE 2.2b — BASIN-DRIFT GATE")
    print("="*70)
    test_basin_on()
    test_basin_off()
    asyncio.run(test_lexical())
    test_stalled()
    print("\n" + "="*70)
    print("  ALL PASS")
    print("="*70)
