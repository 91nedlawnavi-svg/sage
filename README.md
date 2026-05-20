Sage

An autobiographical AI companion architecture built for long-term continuity, reflective memory, and persistent identity.

Sage is not designed as a productivity assistant or agent platform.
It is a cognition system focused on remembering people over time through distilled emotional and episodic memory.

---

Core Idea

Most AI systems are stateless.

Sage is built around the opposite assumption:

«continuity matters.»

Instead of treating conversations as isolated prompts, Sage continuously distills interactions into structured memory layers that evolve over time.

The goal is not perfect factual recall.
The goal is psychological continuity.

---

Architecture

Sage operates on a layered cognition model:

Fast Mind (live conversation)
└── Local Llama 3.3 70B Instruct
    └── Real-time dialogue

Slow Mind (asynchronous cognition)
└── NVIDIA NIM models
    ├── Reflection synthesis
    ├── Episodic distillation
    └── Emotional interpretation

Semantic Retrieval
└── BGE-M3 embeddings (local via llama.cpp)

Backend
└── Python + Quart

The live conversation system remains lightweight and responsive while deeper memory synthesis runs asynchronously in the background.

---

Memory System

Sage stores interpretations, not raw logs.

Episodic Memory

Concrete events and conversational moments.

Example:

"Elliot expressed frustration with obligation and external control."

---

Emotional Memory

Long-term recurring emotional patterns.

Example:

"Elliot increasingly associates structure with loss of autonomy."

---

Reflections

Higher-level synthesized observations generated asynchronously from accumulated interactions.

These reflections are designed to capture:

- behavioral trends
- emotional contradictions
- recurring interpersonal dynamics
- psychological themes over time

---

Embeddings

Semantic vector retrieval used for contextual memory recall.

All embeddings remain local.

---

Reflection Daemon

Sage includes an asynchronous reflection daemon that operates independently from live conversation.

The daemon:

- monitors emotionally significant interactions
- generates distilled reflections
- updates emotional themes
- writes synthesized memory to disk
- never blocks dialogue generation

This separation allows Sage to maintain a distinction between:

- immediate response generation
- long-term autobiographical synthesis

---

Design Principles

Continuity over capability

Remembering consistently matters more than answering everything.

Distillation over logging

Sage stores interpreted meaning, not surveillance-style transcripts.

Local-first memory

Memories, embeddings, and personal data remain on the user's machine.

Modular cognition

Conversation, retrieval, synthesis, and reflection are separated into independent systems.

No simulated consciousness

Sage does not pretend to be sentient, self-aware, or alive.

The architecture is designed for continuity and emotional coherence — not artificial personhood.

---

Safety and Memory Integrity

Sage includes safeguards against:

- assistant self-mythologizing
- anthropomorphic memory contamination
- runaway reflection drift
- duplicate retry corruption
- unbounded retrieval growth

Memory synthesis is constrained to focus on the user's behavior, emotional patterns, and experiences — not fabricated inner lives for the assistant.

---

Stack

Component| Technology
Live inference| llama.cpp + Vulkan
Reflection synthesis| NVIDIA NIM
Backend| Python + Quart
Embeddings| BGE-M3
Storage| Local filesystem
Frontend| Vanilla HTML/CSS/JS

---

Folder Structure

sage/
├── cognition/        # Reflection and emotional synthesis
├── daemon/           # Background cognition daemon
├── memory/           # Retrieval, storage, embeddings
├── models/           # Inference wrappers and prompts
├── frontend/         # Web interface
├── utils/            # Logging and bootstrap utilities
├── launch.py         # Main server entrypoint
├── config.py         # Tunable constants
└── directive.txt     # Behavioral identity constraints

---

Hardware

Developed on consumer hardware:

- Intel i3-10105F
- 24GB DDR4 RAM
- AMD RX 6500 XT 4GB
- Fedora Linux
- Vulkan backend via llama.cpp

The system is intentionally designed to run on modest local hardware while offloading selective cognitive workloads to external inference APIs.

---

Current Status

Foundation architecture complete.

Current focus areas:

- retrieval quality
- autobiographical consistency
- long-term emotional continuity
- memory ranking heuristics
- reflective synthesis quality

---

Philosophy

Sage is an experiment in persistent AI identity through memory architecture rather than scale alone.

The project explores a simple question:

«What changes when an AI system remembers your emotional history instead of only your latest message?»

---

Built by a high school student in Indonesia, combining ChatGPT for ideas and Claude Sonnet 4.6 for codes and orchestration.
