"""Embedder-swap threshold calibration — Blueprint §2.7 (e5-large-v2 → Qwen3-0.6B).

Does three things in one pass, all against the CANDIDATE server (:8181) and a
temp output file — never touches ~/sage_data (Invariant 4):

1. Re-embeds every recall_index.jsonl text with Qwen3 and writes a swap-ready
   index to /tmp/qwen3_recall_index.jsonl (same schema, texts unchanged).
2. Percentile-maps each e5-calibrated threshold onto the Qwen3 similarity
   scale: for each old threshold, find its percentile in the e5 pairwise-sim
   distribution over the stored vectors, then read off the same percentile in
   the Qwen3 pairwise distribution over the SAME texts.
3. Probe report: hand-written EN + Bahasa Indonesia queries embedded with the
   Qwen3 query instruction, top-5 index hits each — eyeball check that the
   mapped recall floor separates real hits from noise, and that cross-language
   retrieval works.

Usage: python -m bench.threshold_calibration [port]   (default 8181)
"""
import json
import sys
import urllib.request

import numpy as np

from config.settings import RECALL_INDEX_PATH

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8181
URL = f"http://127.0.0.1:{PORT}/embedding"
OUT_PATH = "/tmp/qwen3_recall_index.jsonl"

# Same convention the post-swap code will use: passages plain, queries
# carry the Qwen3 retrieval instruction.
QUERY_PREFIX = ("Instruct: Given a query about the user's life and past "
                "conversations, retrieve relevant memories\nQuery: ")

# (query, note) — note is for the human reading the report.
PROBES = [
    ("what happened at my mother's funeral", "EN → family/funeral memories"),
    ("my brother's relapse", "EN → brother/substance memories"),
    ("how is my new job going", "EN → work memories"),
    ("what does Sage wonder about when alone", "EN → reflections"),
    ("adik laki-laki saya mencuri dari saya", "ID: my younger brother stole from me"),
    ("pemakaman ibu saya", "ID: my mother's funeral"),
    ("pekerjaan baru saya", "ID: my new job"),
]

# threshold name -> e5-calibrated value currently in settings
OLD_THRESHOLDS = {
    "RECALL_MIN_SIM": 0.70,
    "KNOWLEDGE_FACT_MIN_SIM": 0.73,
    "BASIN_SIM_THRESHOLD": 0.80,
    "NOVELTY_SIM_THRESHOLD": 0.82,
    "REFLECTION_BASIN_SIM_THRESHOLD": 0.88,
}


def embed(text: str) -> list[float]:
    req = urllib.request.Request(
        URL, data=json.dumps({"content": text}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    return data[0]["embedding"][0]


def pairwise_sims(mat: np.ndarray) -> np.ndarray:
    """Upper-triangle cosine sims of row-normalized matrix."""
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    n = mat / norms
    sims = n @ n.T
    iu = np.triu_indices(len(mat), k=1)
    return sims[iu]


def main() -> int:
    entries = []
    with open(RECALL_INDEX_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    print(f"index entries: {len(entries)}")

    # 1. re-embed everything (passages plain — no prefix)
    new_entries = []
    for i, e in enumerate(entries):
        v = embed(e["text"])
        ne = dict(e)
        ne["embedding"] = v
        new_entries.append(ne)
        if (i + 1) % 100 == 0:
            print(f"  embedded {i + 1}/{len(entries)}")
    with open(OUT_PATH, "w") as f:
        for ne in new_entries:
            f.write(json.dumps(ne, ensure_ascii=False) + "\n")
    print(f"wrote {OUT_PATH}")

    e5 = np.array([e["embedding"] for e in entries], dtype=np.float32)
    q3 = np.array([e["embedding"] for e in new_entries], dtype=np.float32)
    e5_sims = np.sort(pairwise_sims(e5))
    q3_sims = np.sort(pairwise_sims(q3))
    print(f"pairwise sims: e5 median={np.median(e5_sims):.4f} "
          f"p95={np.percentile(e5_sims, 95):.4f} | "
          f"qwen3 median={np.median(q3_sims):.4f} "
          f"p95={np.percentile(q3_sims, 95):.4f}")

    # 2. percentile map
    print("\n-- percentile-mapped thresholds --")
    for name, old in sorted(OLD_THRESHOLDS.items(), key=lambda kv: kv[1]):
        pct = float(np.searchsorted(e5_sims, old)) / len(e5_sims) * 100.0
        mapped = float(np.percentile(q3_sims, pct))
        print(f"{name}: e5 {old:.2f} (p{pct:.1f}) -> qwen3 {mapped:.4f}")

    # 3. probe report
    qn = q3 / np.clip(np.linalg.norm(q3, axis=1, keepdims=True), 1e-9, None)
    print("\n-- probe report (top 5 each) --")
    for query, note in PROBES:
        qv = np.array(embed(QUERY_PREFIX + query), dtype=np.float32)
        qv /= max(float(np.linalg.norm(qv)), 1e-9)
        sims = qn @ qv
        top = np.argsort(sims)[::-1][:5]
        print(f"\n[{note}] \"{query}\"")
        for idx in top:
            t = new_entries[idx]["text"][:90].replace("\n", " ")
            print(f"  {sims[idx]:.4f}  ({new_entries[idx]['kind']})  {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
