"""Skill write-origin provenance tracking.

ContextVar for distinguishing background-review skill writes from
foreground user-directed writes. The background review fork sets
the origin to "background_review" so skill_manage guards can
restrict autonomous writes to agent-created skills only.

Design: Hermes skill_provenance.py pattern adapted for nanobot.
"""

from __future__ import annotations

import contextvars
from pathlib import Path

# Write origin constants
BACKGROUND_REVIEW = "background_review"
ASSISTANT_TOOL = "assistant_tool"

# ContextVar: current write origin
_current_write_origin: contextvars.ContextVar[str] = contextvars.ContextVar(
    "skill_write_origin", default=ASSISTANT_TOOL
)

# ContextVar: paths read by background review fork (read-before-write guard)
_background_review_read_paths: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "background_review_read_paths", default=frozenset()
)


def set_current_write_origin(origin: str) -> contextvars.Token[str]:
    """Set the current write origin. Returns token for reset."""
    return _current_write_origin.set(origin)


def reset_current_write_origin(token: contextvars.Token[str]) -> None:
    """Reset write origin to previous value."""
    _current_write_origin.reset(token)


def get_current_write_origin() -> str:
    """Get the current write origin."""
    return _current_write_origin.get()


def is_background_review() -> bool:
    """Check if current context is a background review fork."""
    return _current_write_origin.get() == BACKGROUND_REVIEW


def mark_background_review_skill_read(path: Path) -> None:
    """Record that the background review fork has read a skill file.

    The review fork must read a skill before modifying it, ensuring
    it works with actual content rather than inferred knowledge.
    """
    if not is_background_review():
        return
    try:
        resolved = str(path.resolve())
    except Exception:
        resolved = str(path)
    current = set(_background_review_read_paths.get())
    current.add(resolved)
    _background_review_read_paths.set(frozenset(current))


def background_review_has_read(path: Path) -> bool:
    """Check if the background review fork has read the given path."""
    if not is_background_review():
        return True  # Guard only applies to background review
    try:
        resolved = str(path.resolve())
    except Exception:
        resolved = str(path)
    return resolved in _background_review_read_paths.get()


def reset_background_review_read_marks() -> None:
    """Clear read-before-write marks for the current context."""
    _background_review_read_paths.set(frozenset())
