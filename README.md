# Sage

A local AI companion built for autobiographical continuity, emotional memory, and persistent identity — not productivity.

---

## What Sage Is

Sage is a personal AI companion architecture designed to remember you over time. Not your tasks. Not your calendar. You — your patterns, your emotional state, your recurring thoughts, the people you mention, the things that matter.

She is built to be a presence, not a tool.

---

## What Sage Is Not

- Not a productivity assistant
- Not an enterprise chatbot
- Not an agent swarm
- Not a web search wrapper
- Not a generic AI interface

---

## Architecture

Sage runs on a layered cognition model — a fast mind for live conversation and a slow mind for reflection.

```
Fast Mind (live)
└── NVIDIA NIM — Llama 4 Maverick (main conversation)

Slow Mind (async daemon)
└── Qwen 2.5 7B — local via llama.cpp (reflection + memory distillation)

Retrieval
└── BGE-M3 — local via llama.cpp (multilingual semantic embeddings)

Backend
└── Python + Quart (async, WebSocket streaming)
```

### Memory Layers

Sage stores distilled interpretations — not raw transcripts.

| Layer | Purpose |
|---|---|
| Episodic | What happened, when |
| Emotional | Patterns, recurring feelings, underlying states |
| Reflections | Synthesized understanding written by the daemon |
| Embeddings | Semantic vectors for retrieval |

**Bad memory:** `"Elliot said school was annoying."`  
**Good memory:** `"Elliot increasingly associates obligation with loss of autonomy."`

### Reflection Daemon

Runs asynchronously after emotionally significant conversations or every N turns. Never blocks live dialogue. Writes deeper interpretations to disk while Sage continues talking.

---

## Stack

- **Inference:** llama.cpp (Vulkan backend) + NVIDIA NIM API
- **Backend:** Python, Quart
- **Embeddings:** BGE-M3 Q4_K_M via llama.cpp
- **Memory storage:** Local filesystem (JSON + plaintext)
- **Frontend:** Served from `/frontend`

---

## Folder Structure

```
sage/
├── cognition/        # Emotional analysis, synthesis
├── daemon/           # Reflection daemon
├── memory/           # Episodic, emotional, retrieval, storage
├── models/           # Inference wrappers, prompt builders
├── utils/            # Logger, bootstrap
├── frontend/         # UI (HTML)
├── config.py         # All tuneable constants
├── launch.py         # Entry point
├── boot.sh           # Starts local model servers + backend
└── directive.txt     # Sage's identity and rules
```

---

## Hardware

Developed on modest consumer hardware:

- Intel i3-10105F
- 24GB DDR4 RAM
- AMD RX 6500 XT 4GB VRAM
- Linux (Fedora), Vulkan backend

Local models run via llama.cpp Vulkan. Main conversation model runs via NVIDIA NIM free tier.

---

## Design Principles

- **Continuity over capability** — Sage remembering matters more than Sage knowing everything
- **Distillation over logging** — Emotional interpretation, not transcripts
- **Local-first** — Memory and embeddings stay on your machine
- **Modular cognition** — Fast and slow mind separated by design
- **No fake consciousness** — Sage knows what she is

---

## Status

Active development. Foundation architecture complete. Current focus: retrieval quality, autobiographical continuity, and long-term identity coherence.

---

*Built by a high school student in Indonesia.*
