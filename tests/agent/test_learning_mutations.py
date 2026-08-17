"""Tests for learning_mutations.py - learning graph edit operations."""

import pytest
from unittest.mock import Mock

from nanobot.agent.learning_mutations import (
    archive_skill,
    revive_skill,
    edit_memory_entry,
    delete_memory_entry,
    add_memory_entry,
    get_node_details,
)
from nanobot.agent.skill_usage import SkillUsageRecord, STATE_ACTIVE, STATE_ARCHIVED, STATE_STALE


@pytest.fixture
def mock_curator():
    """Create a mock Curator."""
    curator = Mock()
    curator.usage_store = Mock()
    curator.revive_skill = Mock()
    return curator


@pytest.fixture
def mock_entry_store():
    """Create a mock MemoryEntryStore."""
    store = Mock()
    store.entries = ["Entry 1", "Entry 2", "Entry 3"]
    store.replace = Mock()
    store.remove = Mock()
    store.add = Mock()
    return store


def test_archive_skill_success(mock_curator):
    """Test archiving a skill successfully."""
    rec = SkillUsageRecord(name="test-skill", state=STATE_ACTIVE)
    mock_curator.usage_store.load.return_value = rec
    
    result = archive_skill(mock_curator, "test-skill")
    
    assert result["success"] is True
    assert rec.state == STATE_ARCHIVED
    mock_curator.usage_store.save.assert_called_once_with(rec)


def test_archive_skill_already_archived(mock_curator):
    """Test archiving an already archived skill."""
    rec = SkillUsageRecord(name="test-skill", state=STATE_ARCHIVED)
    mock_curator.usage_store.load.return_value = rec
    
    result = archive_skill(mock_curator, "test-skill")
    
    assert result["success"] is False
    assert "already archived" in result["error"]


def test_revive_skill_success(mock_curator):
    """Test reviving a skill successfully."""
    mock_curator.revive_skill.return_value = True
    
    result = revive_skill(mock_curator, "test-skill")
    
    assert result["success"] is True
    assert "revived" in result["message"]


def test_revive_skill_already_active(mock_curator):
    """Test reviving an already active skill."""
    mock_curator.revive_skill.return_value = False
    
    result = revive_skill(mock_curator, "test-skill")
    
    assert result["success"] is False
    assert "already ACTIVE" in result["error"]


def test_edit_memory_entry_success(mock_entry_store):
    """Test editing a memory entry successfully."""
    mock_entry_store.replace.return_value = {"success": True, "message": "Replaced"}
    
    result = edit_memory_entry(mock_entry_store, "old text", "new content")
    
    assert result["success"] is True
    mock_entry_store.replace.assert_called_once_with("old text", "new content")


def test_edit_memory_entry_failure(mock_entry_store):
    """Test editing a memory entry with failure."""
    mock_entry_store.replace.return_value = {"success": False, "error": "Not found"}
    
    result = edit_memory_entry(mock_entry_store, "nonexistent", "new content")
    
    assert result["success"] is False


def test_delete_memory_entry_success(mock_entry_store):
    """Test deleting a memory entry successfully."""
    mock_entry_store.remove.return_value = {"success": True, "message": "Removed"}
    
    result = delete_memory_entry(mock_entry_store, "old text")
    
    assert result["success"] is True
    mock_entry_store.remove.assert_called_once_with("old text")


def test_add_memory_entry_success(mock_entry_store):
    """Test adding a memory entry successfully."""
    mock_entry_store.add.return_value = {"success": True, "message": "Added"}
    
    result = add_memory_entry(mock_entry_store, "new content")
    
    assert result["success"] is True
    mock_entry_store.add.assert_called_once_with("new content")


def test_get_node_details_skill(mock_curator):
    """Test getting skill node details."""
    rec = SkillUsageRecord(
        name="test-skill",
        use_count=10,
        view_count=5,
        state=STATE_ACTIVE,
        created_by="user",
        pinned=False,
    )
    mock_curator.usage_store.load.return_value = rec
    
    result = get_node_details(mock_curator, None, "skill", "test-skill")
    
    assert result["success"] is True
    assert result["type"] == "skill"
    assert result["name"] == "test-skill"
    assert result["use_count"] == 10


def test_get_node_details_memory(mock_curator, mock_entry_store):
    """Test getting memory node details."""
    result = get_node_details(mock_curator, mock_entry_store, "memory", 1)
    
    assert result["success"] is True
    assert result["type"] == "memory"
    assert result["index"] == 1
    assert result["content"] == "Entry 2"


def test_get_node_details_memory_out_of_range(mock_curator, mock_entry_store):
    """Test getting memory node with out of range index."""
    result = get_node_details(mock_curator, mock_entry_store, "memory", 99)
    
    assert result["success"] is False
    assert "out of range" in result["error"]


def test_get_node_details_memory_no_store(mock_curator):
    """Test getting memory node without entry store."""
    result = get_node_details(mock_curator, None, "memory", 0)
    
    assert result["success"] is False
    assert "not available" in result["error"]


def test_get_node_details_unknown_type(mock_curator):
    """Test getting node with unknown type."""
    result = get_node_details(mock_curator, None, "unknown", "test")
    
    assert result["success"] is False
    assert "Unknown node type" in result["error"]
