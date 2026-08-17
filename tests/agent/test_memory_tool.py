"""Tests for nanobot.agent.tools.memory_tool — bounded curated memory with §-delimited entries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.agent.tools.memory_tool import (
    DEFAULT_MEMORY_CHAR_LIMIT,
    ENTRY_DELIMITER,
    MemoryEntryStore,
    MemoryTool,
    MemoryToolConfig,
)


# ---------------------------------------------------------------------------
# MemoryEntryStore
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_file(tmp_path: Path) -> Path:
    return tmp_path / "memory" / "MEMORY.md"


@pytest.fixture
def store(memory_file: Path) -> MemoryEntryStore:
    s = MemoryEntryStore(memory_file, char_limit=1000)
    s.load_from_disk()
    return s


class TestMemoryEntryStore:
    def test_load_from_disk_empty(self, store: MemoryEntryStore):
        assert store.entries == []
        assert store.get_snapshot() == ""

    def test_add_entry(self, store: MemoryEntryStore):
        result = store.add("User prefers dark mode")
        assert result["success"] is True
        assert "Entry added" in result["message"]
        assert len(store.entries) == 1
        assert store.entries[0] == "User prefers dark mode"

    def test_add_empty_content_fails(self, store: MemoryEntryStore):
        result = store.add("")
        assert result["success"] is False
        assert "empty" in result["error"].lower()

    def test_add_duplicate_is_idempotent(self, store: MemoryEntryStore):
        store.add("fact one")
        result = store.add("fact one")
        assert result["success"] is True
        assert "already exists" in result["message"]
        assert len(store.entries) == 1

    def test_add_exceeds_char_limit(self, store: MemoryEntryStore):
        # Fill up to near limit
        store.add("x" * 900)
        result = store.add("y" * 200)
        assert result["success"] is False
        assert "exceed" in result["error"].lower()
        assert "current_entries" in result

    def test_replace_entry(self, store: MemoryEntryStore):
        store.add("old fact about Python")
        result = store.replace("Python", "updated fact about Rust")
        assert result["success"] is True
        assert "replaced" in result["message"].lower()
        assert store.entries[0] == "updated fact about Rust"

    def test_replace_no_match_fails(self, store: MemoryEntryStore):
        store.add("some fact")
        result = store.replace("nonexistent", "new content")
        assert result["success"] is False
        assert "matched" in result["error"].lower()

    def test_replace_empty_old_text_fails(self, store: MemoryEntryStore):
        result = store.replace("", "new content")
        assert result["success"] is False

    def test_replace_empty_new_content_fails(self, store: MemoryEntryStore):
        store.add("some fact")
        result = store.replace("some", "")
        assert result["success"] is False

    def test_remove_entry(self, store: MemoryEntryStore):
        store.add("fact to remove")
        result = store.remove("fact to remove")
        assert result["success"] is True
        assert "removed" in result["message"].lower()
        assert len(store.entries) == 0

    def test_remove_no_match_fails(self, store: MemoryEntryStore):
        result = store.remove("nonexistent")
        assert result["success"] is False

    def test_remove_empty_old_text_fails(self, store: MemoryEntryStore):
        result = store.remove("")
        assert result["success"] is False

    def test_apply_batch_add_multiple(self, store: MemoryEntryStore):
        ops = [
            {"action": "add", "content": "fact 1"},
            {"action": "add", "content": "fact 2"},
            {"action": "add", "content": "fact 3"},
        ]
        result = store.apply_batch(ops)
        assert result["success"] is True
        assert len(store.entries) == 3

    def test_apply_batch_mixed_operations(self, store: MemoryEntryStore):
        store.add("old fact")
        ops = [
            {"action": "remove", "old_text": "old fact"},
            {"action": "add", "content": "new fact 1"},
            {"action": "add", "content": "new fact 2"},
        ]
        result = store.apply_batch(ops)
        assert result["success"] is True
        assert len(store.entries) == 2
        assert "old fact" not in store.entries

    def test_apply_batch_atomic_on_failure(self, store: MemoryEntryStore):
        store.add("existing")
        ops = [
            {"action": "add", "content": "new 1"},
            {"action": "replace", "old_text": "nonexistent", "content": "new 2"},
        ]
        result = store.apply_batch(ops)
        assert result["success"] is False
        # Original state preserved
        assert len(store.entries) == 1
        assert store.entries[0] == "existing"

    def test_apply_batch_empty_operations_fails(self, store: MemoryEntryStore):
        result = store.apply_batch([])
        assert result["success"] is False

    def test_apply_batch_unknown_action_fails(self, store: MemoryEntryStore):
        ops = [{"action": "unknown", "content": "test"}]
        result = store.apply_batch(ops)
        assert result["success"] is False
        assert "unknown action" in result["error"].lower()

    def test_persistence_across_loads(self, memory_file: Path):
        store1 = MemoryEntryStore(memory_file, char_limit=1000)
        store1.load_from_disk()
        store1.add("persistent fact")
        
        store2 = MemoryEntryStore(memory_file, char_limit=1000)
        store2.load_from_disk()
        assert len(store2.entries) == 1
        assert store2.entries[0] == "persistent fact"

    def test_snapshot_frozen_at_load(self, store: MemoryEntryStore):
        store.add("initial fact")
        snapshot1 = store.get_snapshot()
        store.add("another fact")
        snapshot2 = store.get_snapshot()
        # Snapshot should not change after load
        assert snapshot1 == snapshot2

    def test_consolidation_failure_counter(self, store: MemoryEntryStore):
        store.reset_consolidation_failures()
        assert store._consolidation_failures == 0
        
        # Fill up to near limit so subsequent adds fail
        store.add("x" * 990)
        for i in range(4):
            result = store.add(f"overflow {i}")
            if i < 3:
                assert "consolidate" in result["error"].lower()
            else:
                # After 3 failures, should escalate
                assert "stop retrying" in result["error"].lower()

    def test_multiple_matches_in_replace(self, store: MemoryEntryStore):
        store.add("fact about Python")
        store.add("another fact about Python")
        result = store.replace("Python", "Rust fact")
        assert result["success"] is False
        assert "multiple" in result["error"].lower()

    def test_multiple_matches_in_remove(self, store: MemoryEntryStore):
        store.add("fact about Python")
        store.add("another fact about Python")
        result = store.remove("Python")
        assert result["success"] is False
        assert "multiple" in result["error"].lower()


# ---------------------------------------------------------------------------
# MemoryTool
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_tool(memory_file: Path) -> MemoryTool:
    store = MemoryEntryStore(memory_file, char_limit=1000)
    store.load_from_disk()
    return MemoryTool(store=store)


class TestMemoryTool:
    def test_tool_name(self, memory_tool: MemoryTool):
        assert memory_tool.name == "memory"

    def test_tool_description(self, memory_tool: MemoryTool):
        desc = memory_tool.description
        assert "memory" in desc.lower()
        assert "operations" in desc.lower()

    def test_tool_parameters_schema(self, memory_tool: MemoryTool):
        params = memory_tool.parameters
        assert params["type"] == "object"
        assert "action" in params["properties"]
        assert "content" in params["properties"]
        assert "old_text" in params["properties"]
        assert "operations" in params["properties"]

    @pytest.mark.asyncio
    async def test_execute_add(self, memory_tool: MemoryTool):
        result = await memory_tool.execute(action="add", content="test fact")
        data = json.loads(result)
        assert data["success"] is True
        assert "added" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_execute_add_missing_content(self, memory_tool: MemoryTool):
        result = await memory_tool.execute(action="add")
        data = json.loads(result)
        assert data["success"] is False
        assert "content" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_execute_replace(self, memory_tool: MemoryTool):
        await memory_tool.execute(action="add", content="old fact")
        result = await memory_tool.execute(
            action="replace",
            old_text="old",
            content="new fact"
        )
        data = json.loads(result)
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_execute_replace_missing_old_text(self, memory_tool: MemoryTool):
        result = await memory_tool.execute(action="replace", content="new")
        data = json.loads(result)
        assert data["success"] is False
        assert "old_text" in data["error"]

    @pytest.mark.asyncio
    async def test_execute_remove(self, memory_tool: MemoryTool):
        await memory_tool.execute(action="add", content="fact to remove")
        result = await memory_tool.execute(action="remove", old_text="fact to remove")
        data = json.loads(result)
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_execute_remove_missing_old_text(self, memory_tool: MemoryTool):
        result = await memory_tool.execute(action="remove")
        data = json.loads(result)
        assert data["success"] is False
        assert "old_text" in data["error"]

    @pytest.mark.asyncio
    async def test_execute_unknown_action(self, memory_tool: MemoryTool):
        result = await memory_tool.execute(action="unknown")
        data = json.loads(result)
        assert data["success"] is False
        assert "unknown action" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_execute_batch_operations(self, memory_tool: MemoryTool):
        ops = [
            {"action": "add", "content": "fact 1"},
            {"action": "add", "content": "fact 2"},
        ]
        result = await memory_tool.execute(operations=ops)
        data = json.loads(result)
        assert data["success"] is True
        assert "2 operation" in data["message"]

    @pytest.mark.asyncio
    async def test_execute_batch_invalid_type(self, memory_tool: MemoryTool):
        result = await memory_tool.execute(operations="not a list")
        data = json.loads(result)
        assert data["success"] is False
        assert "list" in data["error"].lower()


# ---------------------------------------------------------------------------
# MemoryToolConfig
# ---------------------------------------------------------------------------


class TestMemoryToolConfig:
    def test_default_values(self):
        config = MemoryToolConfig()
        assert config.enable is True
        assert config.memory_char_limit == DEFAULT_MEMORY_CHAR_LIMIT

    def test_custom_values(self):
        config = MemoryToolConfig(enable=False, memory_char_limit=8000)
        assert config.enable is False
        assert config.memory_char_limit == 8000


# ---------------------------------------------------------------------------
# ENTRY_DELIMITER
# ---------------------------------------------------------------------------


class TestEntryDelimiter:
    def test_delimiter_format(self):
        assert ENTRY_DELIMITER == "\n§\n"

    def test_delimiter_splits_entries(self):
        content = "entry 1\n§\nentry 2\n§\nentry 3"
        entries = content.split(ENTRY_DELIMITER)
        assert len(entries) == 3
        assert entries[0] == "entry 1"
        assert entries[1] == "entry 2"
        assert entries[2] == "entry 3"
