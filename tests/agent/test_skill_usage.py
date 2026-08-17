"""Tests for nanobot.agent.skill_usage — usage tracking and state machine."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from nanobot.agent.skill_usage import (
    DEFAULT_ARCHIVE_AFTER_DAYS,
    DEFAULT_STALE_AFTER_DAYS,
    STATE_ACTIVE,
    STATE_ARCHIVED,
    STATE_STALE,
    SkillUsageRecord,
    SkillUsageStore,
)


# ---------------------------------------------------------------------------
# SkillUsageRecord
# ---------------------------------------------------------------------------


class TestSkillUsageRecord:
    def test_defaults(self):
        rec = SkillUsageRecord(name="test-skill")
        assert rec.name == "test-skill"
        assert rec.use_count == 0
        assert rec.view_count == 0
        assert rec.state == STATE_ACTIVE
        assert rec.created_by == "user"
        assert rec.pinned is False

    def test_to_dict_roundtrip(self):
        rec = SkillUsageRecord(name="s", use_count=5, state=STATE_STALE)
        d = rec.to_dict()
        restored = SkillUsageRecord.from_dict(d)
        assert restored.name == "s"
        assert restored.use_count == 5
        assert restored.state == STATE_STALE

    def test_from_dict_ignores_unknown_fields(self):
        data = {"name": "x", "use_count": 1, "future_field": 42}
        rec = SkillUsageRecord.from_dict(data)
        assert rec.name == "x"
        assert rec.use_count == 1


# ---------------------------------------------------------------------------
# SkillUsageStore
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> SkillUsageStore:
    return SkillUsageStore(tmp_path)


class TestSkillUsageStore:
    def test_creates_usage_dir(self, tmp_path: Path):
        SkillUsageStore(tmp_path)
        assert (tmp_path / "skills" / ".usage").is_dir()

    def test_load_returns_default_for_missing(self, store: SkillUsageStore):
        rec = store.load("nonexistent")
        assert rec.name == "nonexistent"
        assert rec.use_count == 0

    def test_save_and_load_roundtrip(self, store: SkillUsageStore):
        rec = SkillUsageRecord(name="alpha", use_count=3, state=STATE_STALE)
        store.save(rec)
        loaded = store.load("alpha")
        assert loaded.use_count == 3
        assert loaded.state == STATE_STALE

    def test_save_creates_json_file(self, store: SkillUsageStore):
        store.save(SkillUsageRecord(name="beta"))
        p = store._path("beta")
        assert p.exists()
        data = json.loads(p.read_text("utf-8"))
        assert data["name"] == "beta"

    def test_record_use_increments_count(self, store: SkillUsageStore):
        store.record_use("gamma")
        store.record_use("gamma")
        rec = store.load("gamma")
        assert rec.use_count == 2
        assert rec.last_activity_at > 0

    def test_record_use_revives_stale(self, store: SkillUsageStore):
        rec = SkillUsageRecord(name="stale-skill", state=STATE_STALE)
        store.save(rec)
        store.record_use("stale-skill")
        loaded = store.load("stale-skill")
        assert loaded.state == STATE_ACTIVE

    def test_record_view_increments_view_count(self, store: SkillUsageStore):
        store.record_view("delta")
        store.record_view("delta")
        rec = store.load("delta")
        assert rec.view_count == 2
        assert rec.use_count == 0  # views don't affect use_count

    def test_mark_created(self, store: SkillUsageStore):
        store.mark_created("new-skill", created_by="agent")
        rec = store.load("new-skill")
        assert rec.created_by == "agent"
        assert rec.created_at > 0
        assert rec.state == STATE_ACTIVE

    def test_is_agent_created(self, store: SkillUsageStore):
        store.mark_created("agent-skill", created_by="agent")
        store.mark_created("user-skill", created_by="user")
        assert store.is_agent_created("agent-skill") is True
        assert store.is_agent_created("user-skill") is False

    def test_is_protected_pinned(self, store: SkillUsageStore):
        store.set_pinned("pinned-skill", True)
        assert store.is_protected("pinned-skill") is True

    def test_is_protected_builtin(self, store: SkillUsageStore):
        store.mark_created("builtin-skill", created_by="builtin")
        assert store.is_protected("builtin-skill") is True

    def test_is_protected_false_for_normal(self, store: SkillUsageStore):
        store.mark_created("normal-skill", created_by="agent")
        assert store.is_protected("normal-skill") is False

    def test_set_pinned(self, store: SkillUsageStore):
        store.set_pinned("toggle-skill", True)
        assert store.load("toggle-skill").pinned is True
        store.set_pinned("toggle-skill", False)
        assert store.load("toggle-skill").pinned is False

    def test_list_all(self, store: SkillUsageStore):
        store.save(SkillUsageRecord(name="a"))
        store.save(SkillUsageRecord(name="b"))
        store.save(SkillUsageRecord(name="c"))
        records = store.list_all()
        names = {r.name for r in records}
        assert names == {"a", "b", "c"}

    def test_list_all_empty(self, store: SkillUsageStore):
        assert store.list_all() == []

    def test_delete(self, store: SkillUsageStore):
        store.save(SkillUsageRecord(name="to-delete"))
        assert store._path("to-delete").exists()
        store.delete("to-delete")
        assert not store._path("to-delete").exists()

    def test_delete_nonexistent_is_noop(self, store: SkillUsageStore):
        store.delete("ghost")  # should not raise

    def test_load_corrupted_json_returns_default(self, store: SkillUsageStore):
        p = store._path("corrupt")
        p.write_text("not valid json{{{", encoding="utf-8")
        rec = store.load("corrupt")
        assert rec.name == "corrupt"
        assert rec.use_count == 0
