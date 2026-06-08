# Reference Shelf — v1 Primitives to Reimplement

This document lists the v1 primitives that will be reimplemented in later phases.
Sourced from the GitHub backup.

---

## 1. Embedding Cache (model-namespaced) + Filesystem Cache

- **Key structure**: `{model_name}/{content_hash}` — isolates different embedding models
- **Filesystem layout**: `~/sage_data/embeddings/{model_name}/{hash[:2]}/{hash}.json`
- **Atomic writes**: write to `.tmp` then `rename()` for crash safety
- **In-memory LRU**: hot cache with configurable size limit
- **TTL support**: optional time-to-live for cache entries

---

## 2. e5 Asymmetric Prefixes + Cosine Fail-Safe

- **Query prefix**: `"query: "` — prepended to user queries before embedding
- **Passage prefix**: `"passage: "` — prepended to stored documents
- **Cosine similarity**: normalized dot product with fail-safe for zero-norm vectors
- **Threshold gating**: configurable minimum similarity to consider a match

---

## 3. Deterministic Salience Math

- **Decay factor**: `0.92` per time unit (configurable)
- **Floor**: minimum salience (e.g., `0.05`) — never fully forget
- **Ceiling**: maximum salience (e.g., `1.0`) — cap runaway boosting
- **Boost caps**: per-event boost limits (e.g., `+0.15` max per interaction)
- **Recalculation**: periodic full recompute from event log (not incremental only)

---

## 4. Atomic tmp→rename Writes for All Persisted State

- **Pattern**: write to `{path}.tmp.{pid}` → `os.rename(tmp, path)` (atomic on POSIX)
- **Applies to**: memories, embeddings index, salience state, directive, config
- **Crash safety**: partial writes never corrupt readable state
- **No locks needed**: single-writer assumption with atomic rename

---

## 5. Directive-First Prompt Assembly with Labeled Domain Blocks

- **Invariant**: directive text is ALWAYS the first content in system prompt
- **Block labels** (in order):
  1. `[DIRECTIVE]` — core identity (never modified by automation)
  2. `[TIME_CONTEXT]` — current date/time
  3. `[ELLIOT'S MEMORY]` — retrieved personal memories
  4. `[SAGE'S INNER CONTEXT]` — working memory, reflections
  5. `[SEARCH BLOCKS]` — web/search results
  6. `[CONVERSATION HISTORY]` — recent turns
- **No block precedes the directive** — this is non-negotiable

---

## 6. Anti-Attractor Retrieval Cap

- **Problem**: high-salience items dominate retrieval, creating feedback loops
- **Solution**: cap the number of results from any single "attractor" cluster
- **Implementation**: group by cluster/topic, enforce max-per-group in top-K
- **Diversity bonus**: slight score boost for underrepresented clusters