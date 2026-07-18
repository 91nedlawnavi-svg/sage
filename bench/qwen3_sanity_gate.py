"""Qwen3-Embedding-0.6B Vulkan sanity gate — Blueprint §2.7 cutover requirement.

Embeds 3 EN + 3 ID sentences (paired meanings) and verifies cosine
structure on the live candidate endpoint:
  - same-meaning cross-language pairs must score HIGHER than
    different-meaning pairs (both within and across languages)
  - no degenerate output (all-sims-equal, NaN, zero vectors — the failure
    shape of the June-2025 Vulkan bug)
Exit 0 = gate passed (swap may proceed). Exit 1 = gate failed (fallback
to BGE-M3 per blueprint).

Usage: python bench/qwen3_sanity_gate.py [port]   (default 8181)
"""
import json
import math
import sys
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8181
URL = f"http://127.0.0.1:{PORT}/v1/embeddings"

# Query-side instruction prefix (blueprint: required for Qwen3 retrieval).
# For the symmetric sanity check we embed all six as plain passages.
PAIRS = [
    ("The weather in Jakarta is hot and humid today.",
     "Cuaca di Jakarta hari ini panas dan lembap."),
    ("My sister works as a nurse at the hospital.",
     "Kakak perempuan saya bekerja sebagai perawat di rumah sakit."),
    ("I enjoy reading science fiction novels at night.",
     "Saya suka membaca novel fiksi ilmiah di malam hari."),
]


def embed(text: str) -> list[float]:
    req = urllib.request.Request(
        URL,
        data=json.dumps({"input": text, "model": "qwen3-emb"}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return data["data"][0]["embedding"]


def cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def main() -> int:
    texts = [t for pair in PAIRS for t in pair]
    vecs = [embed(t) for t in texts]

    # degenerate-output checks (Vulkan bug shape)
    for i, v in enumerate(vecs):
        if any(math.isnan(x) for x in v):
            print(f"FAIL degenerate: NaN in embedding {i}")
            return 1
        if all(abs(x) < 1e-9 for x in v):
            print(f"FAIL degenerate: zero vector {i}")
            return 1
    dim = len(vecs[0])
    print(f"dim={dim}")

    same, diff = [], []
    n = len(texts)
    for i in range(n):
        for j in range(i + 1, n):
            s = cos(vecs[i], vecs[j])
            if i // 2 == j // 2:
                same.append((s, i, j))
            else:
                diff.append((s, i, j))

    for s, i, j in same:
        print(f"  same-meaning  [{i},{j}] cos={s:.4f}")
    dmax = max(diff)[0]
    dmin = min(same)[0]
    print(f"  diff-meaning  max={dmax:.4f}  (n={len(diff)})")

    if len(set(round(s, 6) for s, _, _ in same + diff)) == 1:
        print("FAIL degenerate: all similarities identical")
        return 1
    if dmin <= dmax:
        print(f"FAIL structure: weakest same-meaning ({dmin:.4f}) "
              f"<= strongest diff-meaning ({dmax:.4f})")
        return 1

    print(f"PASS sanity gate: same-meaning floor {dmin:.4f} > "
          f"diff-meaning ceiling {dmax:.4f}, margin {dmin - dmax:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
