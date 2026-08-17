"""Tests for skills_tool.py - LLM read-only skills viewing tool."""

import pytest
from pathlib import Path
from unittest.mock import Mock

from nanobot.agent.tools.skills_tool import SkillsTool
from nanobot.agent.skill_usage import SkillUsageStore, SkillUsageRecord, STATE_ACTIVE


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace with skills."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skills_dir = workspace / "skills"
    skills_dir.mkdir()
    
    # Create test skills
    skill1_dir = skills_dir / "test-skill-1"
    skill1_dir.mkdir()
    (skill1_dir / "SKILL.md").write_text("# Test Skill 1\nDescription 1")
    
    skill2_dir = skills_dir / "test-skill-2"
    skill2_dir.mkdir()
    (skill2_dir / "SKILL.md").write_text("# Test Skill 2\nDescription 2")
    
    return workspace


@pytest.fixture
def usage_store(temp_workspace):
    """Create a SkillUsageStore."""
    return SkillUsageStore(temp_workspace)


@pytest.fixture
def skills_loader(temp_workspace):
    """Create a mock SkillsLoader."""
    loader = Mock()
    loader.list_skills.return_value = [
        {"name": "test-skill-1", "source": "workspace"},
        {"name": "test-skill-2", "source": "workspace"},
    ]
    loader.get_skill_description.side_effect = lambda name: f"Description for {name}"
    loader.load_skill.side_effect = lambda name: f"# {name}\nContent"
    return loader


@pytest.mark.asyncio
async def test_skills_tool_list(skills_loader, usage_store):
    """Test listing skills."""
    tool = SkillsTool(skills_loader, usage_store)
    
    result = await tool.execute(action="list")
    
    assert "test-skill-1" in result
    assert "test-skill-2" in result
    assert "Description for test-skill-1" in result


@pytest.mark.asyncio
async def test_skills_tool_view(skills_loader, usage_store):
    """Test viewing a specific skill."""
    tool = SkillsTool(skills_loader, usage_store)
    
    result = await tool.execute(action="view", name="test-skill-1")
    
    assert "test-skill-1" in result
    assert "Content" in result
    # Verify view was recorded
    rec = usage_store.load("test-skill-1")
    assert rec.view_count == 1


@pytest.mark.asyncio
async def test_skills_tool_view_not_found(usage_store):
    """Test viewing a non-existent skill."""
    loader = Mock()
    loader.load_skill.return_value = None
    tool = SkillsTool(loader, usage_store)
    
    result = await tool.execute(action="view", name="nonexistent")
    
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_skills_tool_view_requires_name(skills_loader, usage_store):
    """Test that view action requires a name parameter."""
    tool = SkillsTool(skills_loader, usage_store)
    
    result = await tool.execute(action="view")
    
    assert "required" in result.lower()


@pytest.mark.asyncio
async def test_skills_tool_invalid_action(skills_loader, usage_store):
    """Test invalid action."""
    tool = SkillsTool(skills_loader, usage_store)
    
    result = await tool.execute(action="invalid")
    
    assert "unknown action" in result.lower()


@pytest.mark.asyncio
async def test_skills_tool_list_with_usage_stats(skills_loader, usage_store):
    """Test that list shows usage statistics."""
    # Set up some usage data
    rec = SkillUsageRecord(name="test-skill-1", use_count=5, state=STATE_ACTIVE)
    usage_store.save(rec)
    
    tool = SkillsTool(skills_loader, usage_store)
    result = await tool.execute(action="list")
    
    assert "uses: 5" in result
    assert "state: active" in result
