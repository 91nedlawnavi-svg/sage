"""
cognition/reflection.py — Public interface for reflection generation

Thin re-export so daemon and other callers import from a stable location.
The actual logic lives in cognition/synthesis.py.
"""

from cognition.synthesis import extract_episode, generate_reflection

__all__ = ["extract_episode", "generate_reflection"]
