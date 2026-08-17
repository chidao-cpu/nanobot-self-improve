"""Tests for nanobot.agent.curator — automatic skill lifecycle management."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from nanobot.agent.curator import (
    CURATOR_REVIEW_PROMPT,
    Curator,
    CuratorConfig,
    apply_automatic_transitions,
)
from nanobot.agent.skill_usage import (
    DEFAULT_ARCHIVE_AFTER_DAYS,
    DEFAULT_STALE_AFTER_DAYS,
    STATE_ACTIVE,
    STATE_ARCHIVED,
    STATE_STALE,
    SkillUsageRecord,
    SkillUsageStore,
)
from nanobot.agent.skills import SkillsLoader


# ---------------------------------------------------------------------------
# apply_automatic_transitions (pure function)
# ---------------------------------------------------------------------------


class TestApplyAutomaticTransitions:
    def test_active_to_stale_after_threshold(self):
        now = time.time()
        rec = SkillUsageRecord(
            name="old-skill",
            state=STATE_ACTIVE,
            created_by="agent",
            last_activity_at=now - (DEFAULT_STALE_AFTER_DAYS + 1) * 86400,
        )
        config = CuratorConfig()
        result = apply_automatic_transitions([rec], now, config)
        assert result[0].state == STATE_STALE

    def test_stale_to_archived_after_threshold(self):
        now = time.time()
        rec = SkillUsageRecord(
            name="very-old",
            state=STATE_STALE,
            created_by="agent",
            last_activity_at=now - (DEFAULT_ARCHIVE_AFTER_DAYS + 1) * 86400,
        )
        config = CuratorConfig()
        result = apply_automatic_transitions([rec], now, config)
        assert result[0].state == STATE_ARCHIVED

    def test_active_stays_active_when_recent(self):
        now = time.time()
        rec = SkillUsageRecord(
            name="fresh",
            state=STATE_ACTIVE,
            created_by="agent",
            last_activity_at=now - 3600,  # 1 hour ago
        )
        config = CuratorConfig()
        result = apply_automatic_transitions([rec], now, config)
        assert result[0].state == STATE_ACTIVE

    def test_skips_user_created_skills(self):
        now = time.time()
        rec = SkillUsageRecord(
            name="user-skill",
            state=STATE_ACTIVE,
            created_by="user",
            last_activity_at=now - (DEFAULT_STALE_AFTER_DAYS + 10) * 86400,
        )
        config = CuratorConfig()
        result = apply_automatic_transitions([rec], now, config)
        assert result[0].state == STATE_ACTIVE

    def test_skips_builtin_skills(self):
        now = time.time()
        rec = SkillUsageRecord(
            name="builtin-skill",
            state=STATE_ACTIVE,
            created_by="builtin",
            last_activity_at=now - (DEFAULT_STALE_AFTER_DAYS + 10) * 86400,
        )
        config = CuratorConfig()
        result = apply_automatic_transitions([rec], now, config)
        assert result[0].state == STATE_ACTIVE

    def test_skips_pinned_skills(self):
        now = time.time()
        rec = SkillUsageRecord(
            name="pinned-skill",
            state=STATE_ACTIVE,
            created_by="agent",
            pinned=True,
            last_activity_at=now - (DEFAULT_STALE_AFTER_DAYS + 10) * 86400,
        )
        config = CuratorConfig()
        result = apply_automatic_transitions([rec], now, config)
        assert result[0].state == STATE_ACTIVE

    def test_dry_run_does_not_mutate(self):
        now = time.time()
        rec = SkillUsageRecord(
            name="dry-run-skill",
            state=STATE_ACTIVE,
            created_by="agent",
            last_activity_at=now - (DEFAULT_STALE_AFTER_DAYS + 1) * 86400,
        )
        config = CuratorConfig(dry_run=True)
        result = apply_automatic_transitions([rec], now, config)
        assert result[0].state == STATE_ACTIVE  # unchanged

    def test_zero_last_activity_treated_as_infinite_idle(self):
        now = time.time()
        rec = SkillUsageRecord(
            name="never-used",
            state=STATE_ACTIVE,
            created_by="agent",
            last_activity_at=0.0,
        )
        config = CuratorConfig()
        result = apply_automatic_transitions([rec], now, config)
        assert result[0].state == STATE_STALE

    def test_multiple_records_processed(self):
        now = time.time()
        records = [
            SkillUsageRecord(
                name="stale-one",
                state=STATE_ACTIVE,
                created_by="agent",
                last_activity_at=now - (DEFAULT_STALE_AFTER_DAYS + 1) * 86400,
            ),
            SkillUsageRecord(
                name="fresh-one",
                state=STATE_ACTIVE,
                created_by="agent",
                last_activity_at=now - 3600,
            ),
        ]
        config = CuratorConfig()
        result = apply_automatic_transitions(records, now, config)
        assert result[0].state == STATE_STALE
        assert result[1].state == STATE_ACTIVE


# ---------------------------------------------------------------------------
# Curator class
# ---------------------------------------------------------------------------


@pytest.fixture
def curator_setup(tmp_path: Path):
    """Create a Curator with usage store and skills loader."""
    usage_store = SkillUsageStore(tmp_path)
    skills_loader = SkillsLoader(tmp_path)
    curator = Curator(usage_store, skills_loader)
    return curator, usage_store


class TestCurator:
    async def test_run_returns_summary(self, curator_setup):
        curator, usage_store = curator_setup
        usage_store.save(SkillUsageRecord(
            name="test-skill",
            state=STATE_ACTIVE,
            created_by="agent",
            last_activity_at=time.time() - 3600,
        ))
        result = await curator.run()
        assert "total_skills" in result
        assert "transitions" in result
        assert result["total_skills"] == 1

    async def test_run_with_transitions(self, curator_setup):
        curator, usage_store = curator_setup
        now = time.time()
        usage_store.save(SkillUsageRecord(
            name="old-skill",
            state=STATE_ACTIVE,
            created_by="agent",
            last_activity_at=now - (DEFAULT_STALE_AFTER_DAYS + 1) * 86400,
        ))
        result = await curator.run()
        assert len(result["transitions"]) == 1
        assert result["transitions"][0]["name"] == "old-skill"
        assert result["transitions"][0]["from"] == STATE_ACTIVE
        assert result["transitions"][0]["to"] == STATE_STALE

    async def test_run_dry_run(self, curator_setup):
        curator, usage_store = curator_setup
        now = time.time()
        usage_store.save(SkillUsageRecord(
            name="dry-skill",
            state=STATE_ACTIVE,
            created_by="agent",
            last_activity_at=now - (DEFAULT_STALE_AFTER_DAYS + 1) * 86400,
        ))
        result = await curator.run(dry_run=True)
        assert result["dry_run"] is True
        # State should not have changed on disk
        loaded = usage_store.load("dry-skill")
        assert loaded.state == STATE_ACTIVE

    def test_get_archived_skills(self, curator_setup):
        curator, usage_store = curator_setup
        usage_store.save(SkillUsageRecord(name="archived-one", state=STATE_ARCHIVED))
        usage_store.save(SkillUsageRecord(name="active-one", state=STATE_ACTIVE))
        archived = curator.get_archived_skills()
        assert "archived-one" in archived
        assert "active-one" not in archived

    def test_revive_skill(self, curator_setup):
        curator, usage_store = curator_setup
        usage_store.save(SkillUsageRecord(name="revive-me", state=STATE_ARCHIVED))
        success = curator.revive_skill("revive-me")
        assert success is True
        loaded = usage_store.load("revive-me")
        assert loaded.state == STATE_ACTIVE

    def test_revive_already_active_returns_false(self, curator_setup):
        curator, usage_store = curator_setup
        usage_store.save(SkillUsageRecord(name="already-active", state=STATE_ACTIVE))
        success = curator.revive_skill("already-active")
        assert success is False


# ---------------------------------------------------------------------------
# CURATOR_REVIEW_PROMPT
# ---------------------------------------------------------------------------


class TestCuratorReviewPrompt:
    def test_prompt_contains_instructions(self):
        assert "umbrella" in CURATOR_REVIEW_PROMPT.lower()
        assert "consolidation" in CURATOR_REVIEW_PROMPT.lower()
        assert "merge" in CURATOR_REVIEW_PROMPT.lower()
        assert "archive" in CURATOR_REVIEW_PROMPT.lower()

    def test_prompt_contains_tool_instructions(self):
        # New prompt uses tool-calling agent instead of JSON response
        assert "skills(action=" in CURATOR_REVIEW_PROMPT
        assert "skill_manage(action=" in CURATOR_REVIEW_PROMPT
        assert "PREFIX CLUSTERS" in CURATOR_REVIEW_PROMPT
