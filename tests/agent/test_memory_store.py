"""Tests for the restructured MemoryStore — pure file I/O layer."""

from pathlib import Path

import pytest

from nanobot.agent.memory import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


class TestMemoryStoreBasicIO:
    def test_read_memory_returns_empty_when_missing(self, store):
        assert store.read_memory() == ""

    def test_write_and_read_memory(self, store):
        store.write_memory("hello")
        assert store.read_memory() == "hello"

    def test_read_soul_returns_empty_when_missing(self, store):
        assert store.read_soul() == ""

    def test_write_and_read_soul(self, store):
        store.write_soul("soul content")
        assert store.read_soul() == "soul content"

    def test_read_user_returns_empty_when_missing(self, store):
        assert store.read_user() == ""

    def test_write_and_read_user(self, store):
        store.write_user("user content")
        assert store.read_user() == "user content"

    def test_get_memory_context_returns_empty_when_missing(self, store):
        assert store.get_memory_context() == ""

    def test_get_memory_context_returns_formatted_content(self, store):
        store.write_memory("important fact")
        ctx = store.get_memory_context()
        assert "Long-term Memory" in ctx
        assert "important fact" in ctx


class TestDreamCursor:
    def test_initial_cursor_is_zero(self, store):
        assert store.get_last_dream_cursor() == 0

    def test_set_and_get_cursor(self, store):
        store.set_last_dream_cursor(5)
        assert store.get_last_dream_cursor() == 5

    def test_cursor_persists(self, store):
        store.set_last_dream_cursor(3)
        store2 = MemoryStore(store.workspace)
        assert store2.get_last_dream_cursor() == 3

    def test_set_cursor_iso_timestamp(self, store):
        store.set_last_dream_cursor("2026-08-17T10:00:00")
        assert store.get_last_dream_cursor() == "2026-08-17T10:00:00"

    def test_set_cursor_none_uses_current_time(self, store):
        store.set_last_dream_cursor(None)
        cursor = store.get_last_dream_cursor()
        # Should be an ISO timestamp string
        assert isinstance(cursor, str)
        assert "T" in cursor

    def test_git_restore_rolls_back_dream_cursor(self, tmp_path):
        store = MemoryStore(tmp_path)
        store.write_memory("before")
        store.set_last_dream_cursor(1)
        assert store.git.init() is True

        store.write_memory("after")
        store.set_last_dream_cursor(2)
        dream_sha = store.git.auto_commit("dream: update")
        assert dream_sha is not None

        store.write_memory("newer")
        store.set_last_dream_cursor(3)

        restore_sha = store.git.revert(dream_sha)

        assert restore_sha is not None
        assert store.read_memory() == "before"
        assert store.get_last_dream_cursor() == 1


class TestRawArchive:
    """raw_archive is now a no-op that logs a warning."""

    def test_raw_archive_is_noop(self, store):
        """raw_archive should not raise and should not write any files."""
        messages = [
            {"content": "message", "timestamp": "2026-01-01", "role": "user"},
        ]
        # Should not raise
        store.raw_archive(messages, session_key="cli:test")
        # No history file should be created
        history_file = store.memory_dir / "history.jsonl"
        assert not history_file.exists()
