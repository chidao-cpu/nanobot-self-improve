"""Skill usage tracking: use_count, state machine, provenance.

Tracks skill usage statistics and manages state transitions for the
self-evolution system. Each skill has a JSON record stored under
workspace/skills/.usage/<name>.json.

State machine: ACTIVE → STALE → ARCHIVED
- ACTIVE: recently used or created
- STALE: idle for DEFAULT_STALE_AFTER_DAYS
- ARCHIVED: idle for DEFAULT_ARCHIVE_AFTER_DAYS (filtered from list_skills)

Safety: atomic writes via temp file + rename to prevent corruption.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from loguru import logger

# State constants
STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"

# Default thresholds (in days)
DEFAULT_STALE_AFTER_DAYS = 30
DEFAULT_ARCHIVE_AFTER_DAYS = 90


@dataclass
class SkillUsageRecord:
    """Per-skill usage statistics and metadata."""

    name: str
    use_count: int = 0
    view_count: int = 0
    last_activity_at: float = 0.0
    created_at: float = 0.0
    created_by: str = "user"  # "user" | "agent" | "builtin"
    state: str = STATE_ACTIVE
    pinned: bool = False
    dynamic_always: bool = False  # Auto-promoted by Curator based on usage

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SkillUsageRecord:
        """Create from dict, ignoring unknown fields."""
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


class SkillUsageStore:
    """Per-skill JSON records under workspace/skills/.usage/.

    Provides atomic read/write operations for skill usage tracking.
    All writes use temp file + rename pattern for crash safety.
    """

    def __init__(self, workspace: Path):
        self.dir = workspace / "skills" / ".usage"
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def workspace(self) -> Path:
        """Return the workspace root (parent of skills/.usage/)."""
        return self.dir.parent.parent

    def _path(self, name: str) -> Path:
        """Get the JSON file path for a skill."""
        return self.dir / f"{name}.json"

    def load(self, name: str) -> SkillUsageRecord:
        """Load a skill's usage record, or create a new one if not found."""
        p = self._path(name)
        if p.exists():
            try:
                data = json.loads(p.read_text("utf-8"))
                return SkillUsageRecord.from_dict(data)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load usage record for {name}: {e}")
        return SkillUsageRecord(name=name)

    def save(self, rec: SkillUsageRecord) -> None:
        """Atomically save a skill's usage record."""
        p = self._path(rec.name)
        tmp = p.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(rec.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(p)
        except OSError as e:
            logger.error(f"Failed to save usage record for {rec.name}: {e}")
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise

    def record_use(self, name: str) -> None:
        """Record a skill usage event (increment use_count, update timestamp)."""
        rec = self.load(name)
        rec.use_count += 1
        rec.last_activity_at = time.time()
        # Revive stale skills on use
        if rec.state == STATE_STALE:
            rec.state = STATE_ACTIVE
        self.save(rec)

    def record_view(self, name: str) -> None:
        """Record a skill view event (increment view_count)."""
        rec = self.load(name)
        rec.view_count += 1
        self.save(rec)

    def mark_created(self, name: str, created_by: str = "user") -> None:
        """Mark a skill as newly created."""
        rec = self.load(name)
        rec.created_at = time.time()
        rec.created_by = created_by
        rec.state = STATE_ACTIVE
        self.save(rec)

    def is_agent_created(self, name: str) -> bool:
        """Check if a skill was created by the agent (vs user/builtin)."""
        return self.load(name).created_by == "agent"

    def is_protected(self, name: str) -> bool:
        """Check if a skill is protected from archival (pinned or builtin)."""
        rec = self.load(name)
        return rec.pinned or rec.created_by == "builtin"

    def set_pinned(self, name: str, pinned: bool) -> None:
        """Set the pinned status of a skill."""
        rec = self.load(name)
        rec.pinned = pinned
        self.save(rec)

    def get_dynamic_always_skills(self) -> list[str]:
        """Get names of skills with dynamic_always=True."""
        return [
            rec.name
            for rec in self.list_all()
            if rec.dynamic_always and rec.state == STATE_ACTIVE
        ]

    def list_all(self) -> list[SkillUsageRecord]:
        """Load all usage records."""
        records = []
        if not self.dir.exists():
            return records
        for p in self.dir.glob("*.json"):
            try:
                data = json.loads(p.read_text("utf-8"))
                records.append(SkillUsageRecord.from_dict(data))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load usage record {p.name}: {e}")
        return records

    def delete(self, name: str) -> None:
        """Delete a skill's usage record."""
        p = self._path(name)
        if p.exists():
            p.unlink()
