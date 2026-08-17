"""Tests for nanobot.agent.tools.skill_manager — create, patch, delete skills."""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.skill_usage import SkillUsageStore
from nanobot.agent.tools.skill_manager import SkillManagerConfig, SkillManagerTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "skills").mkdir()
    return ws


@pytest.fixture
def usage_store(workspace: Path) -> SkillUsageStore:
    return SkillUsageStore(workspace)


@pytest.fixture
def skill_manager(workspace: Path, usage_store: SkillUsageStore) -> SkillManagerTool:
    return SkillManagerTool(workspace=workspace, usage_store=usage_store)


# ---------------------------------------------------------------------------
# SkillManagerTool
# ---------------------------------------------------------------------------


class TestSkillManagerTool:
    def test_tool_name(self, skill_manager: SkillManagerTool):
        assert skill_manager.name == "skill_manage"

    def test_tool_description(self, skill_manager: SkillManagerTool):
        desc = skill_manager.description
        assert "create" in desc.lower()
        assert "skill" in desc.lower()

    def test_tool_parameters_schema(self, skill_manager: SkillManagerTool):
        params = skill_manager.parameters
        assert params["type"] == "object"
        assert "action" in params["properties"]
        assert "name" in params["properties"]
        assert "content" in params["properties"]
        assert "action" in params["required"]
        assert "name" in params["required"]

    @pytest.mark.asyncio
    async def test_create_skill(self, skill_manager: SkillManagerTool, workspace: Path):
        result = await skill_manager.execute(
            action="create",
            name="test-skill",
            content="# Test Skill\n\nThis is a test skill."
        )
        assert "created successfully" in result.lower()
        skill_file = workspace / "skills" / "test-skill" / "SKILL.md"
        assert skill_file.exists()
        assert "Test Skill" in skill_file.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_create_skill_marks_agent_created(
        self, skill_manager: SkillManagerTool, usage_store: SkillUsageStore
    ):
        await skill_manager.execute(
            action="create",
            name="agent-skill",
            content="# Agent Skill"
        )
        assert usage_store.is_agent_created("agent-skill") is True

    @pytest.mark.asyncio
    async def test_create_skill_missing_content(self, skill_manager: SkillManagerTool):
        result = await skill_manager.execute(action="create", name="empty-skill")
        assert result.is_error
        assert "content" in result.lower()

    @pytest.mark.asyncio
    async def test_create_skill_already_exists(
        self, skill_manager: SkillManagerTool, workspace: Path
    ):
        await skill_manager.execute(
            action="create",
            name="existing",
            content="# Existing"
        )
        result = await skill_manager.execute(
            action="create",
            name="existing",
            content="# Duplicate"
        )
        assert result.is_error
        assert "already exists" in result.lower()

    @pytest.mark.asyncio
    async def test_patch_skill(
        self, skill_manager: SkillManagerTool, workspace: Path
    ):
        await skill_manager.execute(
            action="create",
            name="patchable",
            content="# Original"
        )
        result = await skill_manager.execute(
            action="patch",
            name="patchable",
            content="# Updated"
        )
        assert "updated successfully" in result.lower()
        skill_file = workspace / "skills" / "patchable" / "SKILL.md"
        assert "Updated" in skill_file.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_patch_skill_missing_content(self, skill_manager: SkillManagerTool):
        result = await skill_manager.execute(action="patch", name="nonexistent")
        assert result.is_error

    @pytest.mark.asyncio
    async def test_patch_nonexistent_skill(self, skill_manager: SkillManagerTool):
        result = await skill_manager.execute(
            action="patch",
            name="ghost",
            content="# Ghost"
        )
        assert result.is_error
        assert "does not exist" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_skill(
        self, skill_manager: SkillManagerTool, workspace: Path, usage_store: SkillUsageStore
    ):
        await skill_manager.execute(
            action="create",
            name="deletable",
            content="# Deletable"
        )
        result = await skill_manager.execute(action="delete", name="deletable")
        assert "deleted successfully" in result.lower()
        skill_dir = workspace / "skills" / "deletable"
        assert not skill_dir.exists()
        # Check archived in usage store
        rec = usage_store.load("deletable")
        assert rec.state == "archived"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_skill(self, skill_manager: SkillManagerTool):
        result = await skill_manager.execute(action="delete", name="ghost")
        assert result.is_error
        assert "does not exist" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_protected_skill(
        self, skill_manager: SkillManagerTool, usage_store: SkillUsageStore, workspace: Path
    ):
        # Create and pin
        await skill_manager.execute(
            action="create",
            name="protected",
            content="# Protected"
        )
        usage_store.set_pinned("protected", True)
        
        result = await skill_manager.execute(action="delete", name="protected")
        assert result.is_error
        assert "protected" in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_skill_name(self, skill_manager: SkillManagerTool):
        result = await skill_manager.execute(
            action="create",
            name="invalid name with spaces",
            content="# Invalid"
        )
        assert result.is_error
        assert "invalid skill name" in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_skill_name_special_chars(self, skill_manager: SkillManagerTool):
        result = await skill_manager.execute(
            action="create",
            name="../escape",
            content="# Escape"
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unknown_action(self, skill_manager: SkillManagerTool):
        result = await skill_manager.execute(action="unknown", name="test")
        assert result.is_error
        assert "unknown action" in result.lower()


# ---------------------------------------------------------------------------
# SkillManagerConfig
# ---------------------------------------------------------------------------


class TestSkillManagerConfig:
    def test_default_values(self):
        config = SkillManagerConfig()
        assert config.enable is True

    def test_custom_values(self):
        config = SkillManagerConfig(enable=False)
        assert config.enable is False
