"""Memory tool: bounded curated memory with §-delimited entries.

Provides the agent with a persistent memory store that survives across
sessions. Entries are managed via add/replace/remove operations with a
character budget enforced at the final state.

Design: §8-10 of SELF_EVOLUTION_ADAPTATION.md
Reference: Hermes harness-agent memory implementation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.config_base import Base

# § delimiter — must match Hermes exactly for cross-compatibility
ENTRY_DELIMITER = "\n§\n"

# Default character budgets
DEFAULT_MEMORY_CHAR_LIMIT = 4000


class MemoryToolConfig(Base):
    enable: bool = True
    memory_char_limit: int = DEFAULT_MEMORY_CHAR_LIMIT


class MemoryEntryStore:
    """Bounded curated memory with file persistence.

    Maintains two parallel states:
      - _snapshot: frozen at load time, used for system prompt injection.
        Never mutated mid-session. Keeps prefix cache stable.
      - entries: live state, mutated by tool calls, persisted to disk.
        Tool responses always reflect this live state.
    """

    _MAX_CONSOLIDATION_FAILURES_PER_TURN = 3

    def __init__(
        self,
        memory_file: Path,
        char_limit: int = DEFAULT_MEMORY_CHAR_LIMIT,
    ):
        self.memory_file = memory_file
        self.char_limit = char_limit
        self.entries: list[str] = []
        self._snapshot: str = ""
        self._consolidation_failures = 0

    def load_from_disk(self) -> None:
        """Load entries from MEMORY.md, capture frozen snapshot."""
        self.entries = self._read_file(self.memory_file)
        # Deduplicate (preserves order, keeps first occurrence)
        self.entries = list(dict.fromkeys(self.entries))
        # Capture frozen snapshot for system prompt
        self._snapshot = self._render_block(self.entries)

    def reset_consolidation_failures(self) -> None:
        """Reset per-turn counter (call at turn start)."""
        self._consolidation_failures = 0

    # -- Mutations --

    def add(self, content: str) -> dict[str, Any]:
        """Append a new entry. Returns error if it would exceed char limit."""
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        if content in self.entries:
            return self._success_response("Entry already exists (no duplicate added).")

        # Overlap detection: reject if new entry is a substring of an existing
        # entry, or an existing entry is a substring of the new one.
        # This prevents semantic duplicates like "OS: Windows 11" when
        # "OS: Windows 11; Terminal: PowerShell" already exists.
        for existing in self.entries:
            if content in existing or existing in content:
                return {
                    "success": False,
                    "error": (
                        f"New entry overlaps with existing entry: "
                        f"'{existing[:120]}...'. "
                        f"Use 'replace' to update it or 'remove' it first, "
                        f"instead of adding a duplicate."
                    ),
                    "overlapping_entry": existing,
                    "usage": f"{self._char_count():,}/{self.char_limit:,}",
                }

        new_entries = self.entries + [content]
        new_total = len(ENTRY_DELIMITER.join(new_entries))

        if new_total > self.char_limit:
            current = self._char_count()
            return self._consolidation_failure({
                "success": False,
                "error": (
                    f"Memory at {current:,}/{self.char_limit:,} chars. "
                    f"Adding this entry ({len(content)} chars) would exceed the limit. "
                    f"Consolidate now: use 'replace' to merge overlapping entries into "
                    f"shorter ones or 'remove' stale entries (see current_entries below), "
                    f"then retry this add — all in this turn."
                ),
                "current_entries": self.entries,
                "usage": f"{current:,}/{self.char_limit:,}",
            })

        self.entries.append(content)
        self._save_to_disk()
        return self._success_response("Entry added.")

    def replace(self, old_text: str, new_content: str) -> dict[str, Any]:
        """Find entry containing old_text substring, replace with new_content."""
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {"success": False, "error": "new_content cannot be empty. Use 'remove'."}

        matches = [(i, e) for i, e in enumerate(self.entries) if old_text in e]
        if not matches:
            return self._consolidation_failure({
                "success": False,
                "error": (
                    f"No entry matched '{old_text}'. Check current_entries below "
                    f"and retry with the exact text."
                ),
                "current_entries": self.entries,
            })

        if len(matches) > 1:
            unique_texts = {e for _, e in matches}
            if len(unique_texts) > 1:
                return {
                    "success": False,
                    "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                    "matches": [e[:80] + "..." for e in [e for _, e in matches]],
                }

        idx = matches[0][0]
        test_entries = self.entries.copy()
        test_entries[idx] = new_content
        new_total = len(ENTRY_DELIMITER.join(test_entries))

        if new_total > self.char_limit:
            current = self._char_count()
            return self._consolidation_failure({
                "success": False,
                "error": (
                    f"Replacement would put memory at {new_total:,}/{self.char_limit:,} chars. "
                    f"Shorten the new content, or 'remove' stale entries first."
                ),
                "current_entries": self.entries,
                "usage": f"{current:,}/{self.char_limit:,}",
            })

        self.entries[idx] = new_content
        self._save_to_disk()
        return self._success_response("Entry replaced.")

    def remove(self, old_text: str) -> dict[str, Any]:
        """Remove the entry containing old_text substring."""
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}

        matches = [(i, e) for i, e in enumerate(self.entries) if old_text in e]
        if not matches:
            return self._consolidation_failure({
                "success": False,
                "error": (
                    f"No entry matched '{old_text}'. Check current_entries below."
                ),
                "current_entries": self.entries,
            })

        if len(matches) > 1:
            unique_texts = {e for _, e in matches}
            if len(unique_texts) > 1:
                return {
                    "success": False,
                    "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                }

        idx = matches[0][0]
        self.entries.pop(idx)
        self._save_to_disk()
        return self._success_response("Entry removed.")

    def apply_batch(self, operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply a sequence of add/replace/remove ops atomically.

        All-or-nothing: if any op fails or the net result exceeds the char
        limit, nothing is written.
        """
        if not operations:
            return {"success": False, "error": "operations list is empty."}

        working = list(self.entries)

        for i, op in enumerate(operations):
            op = op or {}
            act = op.get("action")
            content = (op.get("content") or "").strip()
            old_text = (op.get("old_text") or "").strip()
            pos = f"Operation {i + 1} ({act or 'unknown'})"

            if act == "add":
                if not content:
                    return self._batch_error(f"{pos}: content is required.")
                if content in working:
                    continue  # idempotent
                working.append(content)

            elif act == "replace":
                if not old_text:
                    return self._batch_error(f"{pos}: old_text is required.")
                if not content:
                    return self._batch_error(f"{pos}: content is required.")
                matches = [j for j, e in enumerate(working) if old_text in e]
                if not matches:
                    return self._batch_error(f"{pos}: no entry matched '{old_text}'.")
                if len({working[j] for j in matches}) > 1:
                    return self._batch_error(
                        f"{pos}: '{old_text}' matched multiple distinct entries."
                    )
                working[matches[0]] = content

            elif act == "remove":
                if not old_text:
                    return self._batch_error(f"{pos}: old_text is required.")
                matches = [j for j, e in enumerate(working) if old_text in e]
                if not matches:
                    return self._batch_error(f"{pos}: no entry matched '{old_text}'.")
                if len({working[j] for j in matches}) > 1:
                    return self._batch_error(
                        f"{pos}: '{old_text}' matched multiple distinct entries."
                    )
                working.pop(matches[0])

            else:
                return self._batch_error(f"{pos}: unknown action '{act}'.")

        # Budget check against FINAL state
        new_total = len(ENTRY_DELIMITER.join(working)) if working else 0
        if new_total > self.char_limit:
            current = self._char_count()
            return self._consolidation_failure({
                "success": False,
                "error": (
                    f"After all {len(operations)} ops, memory would be at "
                    f"{new_total:,}/{self.char_limit:,} chars — over limit. "
                    f"Remove or shorten more entries in the same batch."
                ),
                "current_entries": self.entries,
                "usage": f"{current:,}/{self.char_limit:,}",
            })

        self.entries = working
        self._save_to_disk()
        return self._success_response(f"Applied {len(operations)} operation(s).")

    # -- Snapshot for system prompt --

    def get_snapshot(self) -> str:
        """Return the frozen snapshot captured at load_from_disk() time."""
        return self._snapshot

    # -- Internal helpers --

    def _char_count(self) -> int:
        if not self.entries:
            return 0
        return len(ENTRY_DELIMITER.join(self.entries))

    def _success_response(self, message: str) -> dict[str, Any]:
        self._consolidation_failures = 0
        current = self._char_count()
        pct = min(100, int((current / self.char_limit) * 100)) if self.char_limit > 0 else 0
        return {
            "success": True,
            "done": True,
            "usage": f"{pct}% — {current:,}/{self.char_limit:,} chars",
            "entry_count": len(self.entries),
            "message": message,
            "note": "Write saved. This update is complete — do not repeat it.",
        }

    def _consolidation_failure(self, response: dict[str, Any]) -> dict[str, Any]:
        self._consolidation_failures += 1
        if self._consolidation_failures <= self._MAX_CONSOLIDATION_FAILURES_PER_TURN:
            return response
        return {
            "success": False,
            "done": True,
            "error": (
                f"Memory consolidation failed {self._consolidation_failures} times "
                "this turn. Stop retrying — leave memory unchanged and continue "
                "with your reply to the user."
            ),
        }

    def _batch_error(self, message: str) -> dict[str, Any]:
        current = self._char_count()
        return self._consolidation_failure({
            "success": False,
            "error": message + " No operations were applied (batch is all-or-nothing).",
            "current_entries": self.entries,
            "usage": f"{current:,}/{self.char_limit:,}",
        })

    def _render_block(self, entries: list[str]) -> str:
        """Render a system prompt block with header and usage indicator."""
        if not entries:
            return ""
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / self.char_limit) * 100)) if self.char_limit > 0 else 0
        header = f"MEMORY [{pct}% — {current:,}/{self.char_limit:,} chars]"
        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    def _save_to_disk(self) -> None:
        """Persist entries to MEMORY.md using atomic temp-file + rename."""
        content = ENTRY_DELIMITER.join(self.entries) if self.entries else ""
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.memory_file.with_suffix(self.memory_file.suffix + ".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, self.memory_file)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _read_file(path: Path) -> list[str]:
        """Read a memory file and split into §-delimited entries."""
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return []
        if not raw.strip():
            return []
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
        return [e for e in entries if e]


class MemoryTool(Tool):
    """Memory tool for the nanobot tool registry."""

    config_key = "memory_tool"
    _plugin_discoverable = False  # 手动注册，需要 MemoryEntryStore 引用

    @classmethod
    def config_cls(cls):
        return MemoryToolConfig

    def __init__(self, store: MemoryEntryStore, **kwargs: Any):
        super().__init__(**kwargs)
        self._store = store

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return (
            "Save durable facts to persistent memory that survive across sessions. "
            "Memory is injected into every future turn, so keep entries compact and "
            "high-signal.\n\n"
            "HOW: make ALL your changes in ONE call via an 'operations' array (each "
            "item: {action, content?, old_text?}). The batch applies atomically and "
            "the char limit is checked only on the FINAL result — so a single call "
            "can remove/replace stale entries to free room AND add new ones. Use the "
            "bare action/content/old_text fields only for a single lone change.\n\n"
            "WHEN: save proactively when the user states a preference, correction, or "
            "personal detail, or you learn a stable fact about their environment, "
            "conventions, or workflow. Priority: user preferences & corrections > "
            "environment facts > procedures. The best memory stops the user repeating "
            "themselves.\n\n"
            "IF FULL: an add is rejected with the current entries shown. Reissue as "
            "ONE batch that removes or shortens enough stale entries and adds the new "
            "one together.\n\n"
            "SKIP: trivial/obvious info, easily re-discovered facts, raw data dumps, "
            "task progress, completed-work logs, temporary TODO state. Reusable "
            "procedures belong in a skill, not memory."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "replace", "remove"],
                    "description": (
                        "The action to perform (single-op shape). "
                        "Omit when using 'operations'."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "The entry content. Required for 'add' and 'replace' "
                        "(single-op shape)."
                    ),
                },
                "old_text": {
                    "type": "string",
                    "description": (
                        "REQUIRED for 'replace' and 'remove' (single-op shape): "
                        "a short unique substring identifying the existing entry "
                        "to modify. Omit only for 'add'."
                    ),
                },
                "operations": {
                    "type": "array",
                    "description": (
                        "Batch shape: a list of operations applied atomically in "
                        "one call against the final char budget. Preferred when "
                        "making multiple changes. Each item is "
                        "{action, content?, old_text?}."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["add", "replace", "remove"],
                            },
                            "content": {
                                "type": "string",
                                "description": "Entry content for add/replace.",
                            },
                            "old_text": {
                                "type": "string",
                                "description": (
                                    "Substring identifying the entry for "
                                    "replace/remove."
                                ),
                            },
                        },
                        "required": ["action"],
                    },
                },
            },
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action", "")
        content = kwargs.get("content")
        old_text = kwargs.get("old_text")
        operations = kwargs.get("operations")

        self._store.reset_consolidation_failures()

        # Batch path
        if operations:
            if not isinstance(operations, list):
                return ToolResult(
                    json.dumps({"success": False, "error": "operations must be a list."})
                )
            result = self._store.apply_batch(operations)
            return ToolResult(json.dumps(result, ensure_ascii=False))

        # Single-op path
        if action == "add":
            if not content:
                return ToolResult(
                    json.dumps({"success": False, "error": "Content is required for 'add'."})
                )
            result = self._store.add(content)

        elif action == "replace":
            if not old_text:
                return ToolResult(json.dumps({
                    "success": False,
                    "error": "'replace' needs old_text.",
                    "current_entries": self._store.entries,
                    "usage": f"{self._store._char_count():,}/{self._store.char_limit:,}",
                }, ensure_ascii=False))
            if not content:
                return ToolResult(
                    json.dumps({"success": False, "error": "Content is required for 'replace'."})
                )
            result = self._store.replace(old_text, content)

        elif action == "remove":
            if not old_text:
                return ToolResult(json.dumps({
                    "success": False,
                    "error": "'remove' needs old_text.",
                    "current_entries": self._store.entries,
                }, ensure_ascii=False))
            result = self._store.remove(old_text)

        else:
            return ToolResult(
                json.dumps({"success": False, "error": f"Unknown action '{action}'."})
            )

        return ToolResult(json.dumps(result, ensure_ascii=False))
