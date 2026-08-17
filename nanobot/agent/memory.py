"""Memory system: pure file I/O store and lightweight Consolidator."""

# Tool schemas are installed by the ``@tool_parameters`` class decorator at
# runtime; static analyzers cannot observe that it clears ``parameters`` from
# ``__abstractmethods__`` before these classes are instantiated.
# pyright: reportAbstractUsage=false, reportPrivateUsage=false

from __future__ import annotations

import asyncio
import weakref
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast

from loguru import logger

from nanobot.session.manager import Session, SessionManager
from nanobot.utils.gitstore import GitStore
from nanobot.utils.helpers import (
    content_with_media_breadcrumbs,
    ensure_dir,
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
    find_legal_message_start,
    recent_message_start_index,
    truncate_text,
    truncate_text_to_tokens,
)
from nanobot.utils.prompt_templates import render_template
from nanobot.utils.workspace_prompts import (
    WORKSPACE_PROMPT_MAX_CHARS,
    has_workspace_prompt_override,
    load_workspace_prompt_override,
    workspace_prompt_file,
)

if TYPE_CHECKING:
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.utils.llm_runtime import LLMRuntime

# ---------------------------------------------------------------------------
# MemoryStore — pure file I/O layer
# ---------------------------------------------------------------------------


class DreamRunProgress:
    """Track tool failures that make a nominally completed Dream run unsafe to advance."""

    def __init__(self) -> None:
        self.had_tool_errors = False

    async def __call__(
        self,
        *_args: Any,
        tool_events: list[dict[str, Any]] | None = None,
        **_kwargs: Any,
    ) -> None:
        if any(
            isinstance(cast(object, event), dict) and event.get("phase") == "error"
            for event in tool_events or ()
        ):
            self.had_tool_errors = True


class MemoryStore:
    """Pure file I/O for memory files: MEMORY.md, SOUL.md, USER.md.

    MEMORY.md uses §-delimited entries managed by MemoryEntryStore.
    SOUL.md and USER.md remain as free-form bootstrap files.
    Dream reads session messages directly via build_dream_prompt_from_sessions().
    """

    # Durable files whose real working-tree delta grounds Dream commit messages.
    # Deliberately excludes memory/.dream_cursor so progress bookkeeping never
    # appears as a durable-memory edit in the audit record.
    _DREAM_CONTENT_PATHS = ("SOUL.md", "USER.md", "memory/MEMORY.md")
    # Per-file cap when embedding current contents into the Dream prompt. The
    # durable files are tiny in practice (~5 KB total), but a runaway file must
    # not unbounded the prompt.
    _DREAM_FILE_EMBED_CAP = 8000

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.soul_file = workspace / "SOUL.md"
        self.user_file = workspace / "USER.md"
        self._dream_cursor_file = self.memory_dir / ".dream_cursor"
        self._dream_prompt_oversize_logged = False
        self._git = GitStore(workspace, tracked_files=[
            "SOUL.md", "USER.md", "memory/MEMORY.md", "memory/.dream_cursor",
        ])
        # MemoryEntryStore: §-delimited entry management (initialized externally)
        self._entry_store: Any = None

    @property
    def git(self) -> GitStore:
        return self._git

    def set_entry_store(self, entry_store: Any) -> None:
        """Attach the MemoryEntryStore (called during AgentLoop init)."""
        self._entry_store = entry_store

    # -- generic helpers -----------------------------------------------------

    @staticmethod
    def read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    # -- MEMORY.md (long-term facts) -----------------------------------------

    def read_memory(self) -> str:
        """Read MEMORY.md. Returns frozen snapshot if entry store is attached."""
        if self._entry_store:
            return self._entry_store.get_snapshot()
        # Fallback: raw file read
        return self.read_file(self.memory_file)

    def write_memory(self, content: str) -> None:
        """Write raw content to MEMORY.md (used by Dream tools)."""
        self.memory_file.write_text(content, encoding="utf-8")
        # Reload entry store if attached
        if self._entry_store:
            self._entry_store.load_from_disk()

    # -- SOUL.md -------------------------------------------------------------

    def read_soul(self) -> str:
        return self.read_file(self.soul_file)

    def write_soul(self, content: str) -> None:
        self.soul_file.write_text(content, encoding="utf-8")

    # -- USER.md -------------------------------------------------------------

    def read_user(self) -> str:
        return self.read_file(self.user_file)

    def write_user(self, content: str) -> None:
        self.user_file.write_text(content, encoding="utf-8")

    # -- context injection (used by context.py) ------------------------------

    def get_memory_context(self) -> str:
        long_term = self.read_memory()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    # -- dream cursor --------------------------------------------------------

    def get_last_dream_cursor(self) -> int | str:
        """Return the last Dream processing cursor.

        Returns ISO timestamp (str) if using new format, else int cursor.
        """
        if self._dream_cursor_file.exists():
            with suppress(ValueError, OSError):
                content = self._dream_cursor_file.read_text(encoding="utf-8").strip()
                # Try parsing as ISO timestamp first
                if "T" in content or "-" in content:
                    return content
                # Fallback to int cursor
                return int(content)
        return 0

    def set_last_dream_cursor(self, cursor: int | str | None = None) -> None:
        """Record the Dream processing checkpoint.

        Args:
            cursor: int cursor (legacy) or ISO timestamp (new). If None, uses current time.
        """
        if cursor is None:
            # Use ISO timestamp for new format
            cursor = datetime.now().isoformat()
        self._dream_cursor_file.write_text(str(cursor), encoding="utf-8")

    @property
    def dream_prompt_file(self) -> Path:
        return workspace_prompt_file(self.workspace, "dream")

    def has_dream_prompt_override(self) -> bool:
        return has_workspace_prompt_override(self.dream_prompt_file)

    @staticmethod
    def default_dream_prompt() -> str:
        from nanobot.agent.skills import BUILTIN_SKILLS_DIR

        return render_template(
            "agent/dream.md",
            strip=True,
            skill_creator_path=str(BUILTIN_SKILLS_DIR / "skill-creator" / "SKILL.md"),
        )

    def _dream_template(self) -> str:
        text, original_chars = load_workspace_prompt_override(self.dream_prompt_file)
        if text is not None:
            if (
                original_chars > WORKSPACE_PROMPT_MAX_CHARS
                and not self._dream_prompt_oversize_logged
            ):
                self._dream_prompt_oversize_logged = True
                logger.warning(
                    "workspace Dream prompt exceeds {} chars ({}); truncating. "
                    "Further occurrences suppressed.",
                    WORKSPACE_PROMPT_MAX_CHARS, original_chars,
                )
            return text
        return self.default_dream_prompt()

    def build_dream_prompt_from_sessions(
        self,
        sessions: SessionManager,
        *,
        max_messages: int = 100,
    ) -> tuple[str, str] | None:
        """Build the Dream prompt with session message context.

        New method that reads from session messages instead of history.jsonl.
        Returns ``(prompt, cursor_timestamp)`` or ``None`` if nothing to process.
        """
        context = self.get_dream_context(sessions, max_messages=max_messages)
        if not context:
            return None

        template = self._dream_template()
        files_section = self._render_current_memory_files()
        prompt = (
            f"{template}\n\n{files_section}\n\n"
            f"## Conversation History\n{context}"
        )
        cursor_ts = datetime.now().isoformat()
        return (prompt, cursor_ts)

    def _render_current_memory_files(self) -> str:
        """Render the durable memory files' current contents for the Dream prompt.

        Missing files render as ``(empty)``; oversized files are capped. The
        section is the ground truth the model must edit against.
        """
        files = [
            ("SOUL.md", self.soul_file),
            ("USER.md", self.user_file),
            ("memory/MEMORY.md", self.memory_file),
        ]
        blocks: list[str] = []
        for label, path in files:
            try:
                content = path.read_text(encoding="utf-8") if path.exists() else ""
            except OSError:
                content = ""
            if len(content) > self._DREAM_FILE_EMBED_CAP:
                content = truncate_text(content, self._DREAM_FILE_EMBED_CAP) + "\n...[truncated]"
            blocks.append(f"### {label}\n{content}" if content.strip() else f"### {label}\n(empty)")
        return "## Current Memory Files\n" + "\n\n".join(blocks)

    def dream_content_diff(self) -> str:
        """Structured summary of uncommitted changes to the durable memory files.

        Returns "" when git is unavailable or no content file changed. This is
        the ground-truth input for diff-grounded Dream commit messages.
        """
        if not self._git.is_initialized():
            return ""
        return self._git.summarize_working_tree(list(self._DREAM_CONTENT_PATHS))

    def build_dream_tools(self) -> ToolRegistry:
        """Build the restricted tool registry used by Dream runs."""
        from nanobot.agent.skills import BUILTIN_SKILLS_DIR
        from nanobot.agent.tools.apply_patch import ApplyPatchTool
        from nanobot.agent.tools.file_state import FileStates
        from nanobot.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
        from nanobot.agent.tools.registry import ToolRegistry

        tools = ToolRegistry()
        file_states = FileStates()
        workspace = self.workspace
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        extra_read = [BUILTIN_SKILLS_DIR] if BUILTIN_SKILLS_DIR.exists() else None
        editable_files = [self.memory_file, self.soul_file, self.user_file]

        tools.register(ReadFileTool(
            workspace=workspace,
            allowed_dir=workspace,
            extra_read_allowed_dirs=extra_read,
            file_states=file_states,
        ))
        tools.register(EditFileTool(
            workspace=workspace,
            allowed_dir=skills_dir,
            extra_write_allowed_files=editable_files,
            file_states=file_states,
        ))
        tools.register(ApplyPatchTool(
            workspace=workspace,
            allowed_dir=skills_dir,
            extra_write_allowed_files=editable_files,
            file_states=file_states,
        ))
        tools.register(WriteFileTool(
            workspace=workspace,
            allowed_dir=skills_dir,
            extra_write_allowed_files=editable_files,
            file_states=file_states,
        ))
        return tools

    @staticmethod
    def dream_run_completed(
        resp: object | None,
        *,
        had_tool_errors: bool = False,
    ) -> bool:
        """Return True only when a Dream turn completed without tool failures."""
        metadata = getattr(resp, "metadata", None)
        if had_tool_errors or not isinstance(metadata, dict):
            return False
        return cast(dict[str, Any], metadata).get("_stop_reason") == "completed"

    def get_dream_context(
        self,
        sessions: SessionManager,
        max_messages: int = 100,
    ) -> str | None:
        """Extract unprocessed session messages for Dream input.

        Reads messages that have not yet been compressed (after last_consolidated)
        and are newer than the last Dream cursor timestamp.

        Args:
            sessions: SessionManager to read messages from.
            max_messages: Maximum number of messages to include.

        Returns:
            Formatted conversation history or None if nothing to process.
        """
        since_ts = self.get_last_dream_cursor()
        # Convert cursor to comparable string (ISO timestamp or empty)
        since_str = str(since_ts) if since_ts else ""

        all_messages: list[dict[str, Any]] = []

        # Get all sessions and sort by updated_at descending
        session_list = sessions.list_sessions()
        session_list.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        
        # Process up to 20 most recent sessions
        for session_info in session_list[:20]:
            key = session_info.get("key", "")
            if not key or key.startswith("dream:"):
                continue
            
            try:
                session = sessions.get_or_create(key)
            except Exception:
                continue
            
            # Only read messages after last_consolidated (uncompressed)
            for msg in session.messages[session.last_consolidated:]:
                ts = msg.get("timestamp", "")
                ts_str = str(ts) if ts else ""
                if since_str and ts_str <= since_str:
                    continue
                role = msg.get("role", "")
                content = msg.get("content", "")
                # Skip compressed summary messages
                if msg.get("_compressed_summary"):
                    continue
                if role in ("user", "assistant") and content:
                    all_messages.append({
                        "timestamp": ts_str,
                        "role": role,
                        "content": content_with_media_breadcrumbs(
                            role, content, msg.get("media")
                        ),
                    })

        if not all_messages:
            return None

        all_messages.sort(key=lambda m: m["timestamp"])
        batch = all_messages[-max_messages:]

        lines = []
        for m in batch:
            lines.append(f"[{m['timestamp'][:16]}] {m['role'].upper()}: {m['content']}")
        return "\n".join(lines)

    # -- message formatting utility ------------------------------------------

    @staticmethod
    def _format_messages(messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in messages:
            content = content_with_media_breadcrumbs(
                message.get("role"),
                message.get("content", ""),
                message.get("media"),
            )
            if not content:
                continue
            tools_used = message.get("tools_used")
            tools = (
                f" [tools: {', '.join(cast(list[str], tools_used))}]"
                if tools_used
                else ""
            )
            raw_timestamp = message.get("timestamp")
            timestamp = str(raw_timestamp) if raw_timestamp is not None else "?"
            role = str(message.get("role") or "unknown")
            lines.append(f"[{timestamp[:16]}] {role.upper()}{tools}: {content}")
        return "\n".join(lines)

    def raw_archive(
        self,
        messages: list[dict[str, Any]],
        *,
        max_chars: int | None = None,
        session_key: str | None = None,
    ) -> None:
        """Fallback: log dropped messages without writing to history.jsonl.
        
        This is a safety net when the session exceeds FILE_MAX_MESSAGES and
        compression hasn't run yet. Messages are logged but not persisted,
        since the new ContextCompressor handles inline compression.
        """
        logger.warning(
            "Session {} exceeded file cap: dropping {} messages (raw_archive no-op)",
            session_key or "unknown",
            len(messages),
        )

    # ------------------------------------------------------------------
    # Dream helpers
    # ------------------------------------------------------------------

    @staticmethod
    def dream_session_key() -> str:
        """Return a unique session key for a Dream run, e.g. ``dream:20260528-100000``."""
        return f"dream:{datetime.now():%Y%m%d-%H%M%S}"

    @staticmethod
    def build_dream_commit_message(prefix: str, diff_body: str) -> str:
        """Build a Dream commit message grounded in the real working-tree diff.

        *diff_body* is a structured, machine-derived summary of the actual file
        changes (see :meth:`dream_content_diff` /
        :meth:`GitStore.summarize_working_tree`). The LLM narrative is
        deliberately excluded so the audit record (``/dream-log``) reflects the
        filesystem's truth, not the model's self-report.

        An empty *diff_body* yields the bare *prefix*, which ``auto_commit``
        turns into a no-op when there is nothing to stage.
        """
        diff_body = (diff_body or "").strip()
        if not diff_body:
            return prefix
        return f"{prefix}\n\n{diff_body}"

    @staticmethod
    def prune_dream_sessions(sessions_dir: Path, *, keep: int = 10) -> None:
        """Remove the oldest Dream session files, keeping only the N most recent.

        Only current base64url-encoded Dream session keys are considered.
        Non-dream session files are never touched.
        """
        dream_files: list[Path] = []
        for path in sessions_dir.glob("*.jsonl"):
            decoded_key = SessionManager.decode_storage_key(path.stem)
            if decoded_key is not None and decoded_key.startswith("dream:"):
                dream_files.append(path)
        dream_files.sort(key=lambda p: p.stat().st_mtime)
        if len(dream_files) <= keep:
            return

        to_remove = dream_files[: len(dream_files) - keep]
        for path in to_remove:
            try:
                path.unlink()
                logger.debug("Pruned old dream session: {}", path.stem)
            except OSError:
                logger.warning("Failed to prune dream session {}", path)


# ---------------------------------------------------------------------------
# Consolidator — token estimation and session consolidation helpers
# ---------------------------------------------------------------------------

_RAW_ARCHIVE_MAX_CHARS = 16_000       # fallback dump (LLM failed)
_ARCHIVE_SUMMARY_MAX_CHARS = 8_000    # LLM-produced consolidation summary


class Consolidator:
    """Token estimation and session consolidation helpers."""

    _MAX_CONSOLIDATION_ROUNDS = 5

    _SAFETY_BUFFER = 1024  # extra headroom for tokenizer estimation drift

    def __init__(
        self,
        store: MemoryStore,
        sessions: SessionManager,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        consolidation_ratio: float = 0.5,
        unified_session: bool = False,
    ):
        self.store = store
        self.sessions = sessions
        self.consolidation_ratio = consolidation_ratio
        self.unified_session = unified_session
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """Return the shared consolidation lock for one session."""
        return self._locks.setdefault(session_key, asyncio.Lock())

    def pick_consolidation_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        """Pick a user-turn boundary that removes enough old prompt tokens."""
        start = session.last_consolidated
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    @staticmethod
    def _full_unconsolidated_history(
        session: Session,
    ) -> list[dict[str, Any]]:
        """Return the whole unconsolidated tail for consolidation decisions."""
        unconsolidated_count = len(session.messages) - session.last_consolidated
        if unconsolidated_count <= 0:
            return []
        return session.get_history(max_messages=unconsolidated_count)

    @staticmethod
    def _replay_overflow_boundary(
        session: Session,
        replay_max_messages: int | None,
    ) -> int | None:
        if not replay_max_messages or replay_max_messages <= 0:
            return None
        tail = list(enumerate(session.messages[session.last_consolidated:], session.last_consolidated))
        if len(tail) <= replay_max_messages:
            return None

        tail_messages = [message for _idx, message in tail]
        start_idx = recent_message_start_index(
            tail_messages,
            replay_max_messages,
            extend_to_user=True,
        )
        sliced = tail[start_idx:]
        for i, (_idx, message) in enumerate(sliced):
            if message.get("role") == "user":
                start = i
                if i > 0 and sliced[i - 1][1].get("_channel_delivery"):
                    start = i - 1
                sliced = sliced[start:]
                break

        legal_start = find_legal_message_start([message for _idx, message in sliced])
        if legal_start:
            sliced = sliced[legal_start:]
        if not sliced:
            return len(session.messages)

        first_visible_idx = sliced[0][0]
        if first_visible_idx <= session.last_consolidated:
            return None
        return first_visible_idx

    async def _consolidate_replay_overflow(
        self,
        session: Session,
        replay_max_messages: int | None,
        *,
        runtime: LLMRuntime,
    ) -> str | None:
        """Archive messages that would be hidden by the replay message window."""
        end_idx = self._replay_overflow_boundary(session, replay_max_messages)
        if end_idx is None:
            return None
        chunk = session.messages[session.last_consolidated:end_idx]
        if not chunk:
            return None
        logger.info(
            "Replay-window consolidation for {}: chunk={} msgs, replay_max={}",
            session.key,
            len(chunk),
            replay_max_messages,
        )
        summary = await self.archive(
            chunk,
            runtime=runtime,
            session_key=session.key,
        )
        session.last_consolidated = end_idx
        session.provider_state = None
        self.sessions.save(session)
        return summary

    def _persist_last_summary(self, session: Session, summary: str | None) -> None:
        if summary and summary != "(nothing)":
            session.metadata["_last_summary"] = {
                "text": summary,
                "last_active": session.updated_at.isoformat(),
            }
            self.sessions.save(session)

    def estimate_session_prompt_tokens(
        self,
        session: Session,
        *,
        runtime: LLMRuntime,
    ) -> tuple[int, str]:
        """Estimate prompt size from the full unconsolidated session tail."""
        history = self._full_unconsolidated_history(session)
        channel = session.key.split(":", 1)[0] if ":" in session.key else None
        # Include archived summary in estimation so the budget accounts for it.
        meta = session.metadata.get("_last_summary")
        summary = (
            cast(dict[str, Any], meta).get("text")
            if isinstance(meta, dict)
            else meta
            if isinstance(meta, str)
            else None
        )
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            session_summary=summary,
            session_key=session.key,
            unified_session=self.unified_session,
        )
        return estimate_prompt_tokens_chain(
            runtime.provider,
            runtime.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    def _input_token_budget(self, runtime: LLMRuntime) -> int:
        """Available input token budget for consolidation LLM."""
        return (
            runtime.context_window_tokens
            - runtime.generation.max_tokens
            - self._SAFETY_BUFFER
        )

    def _truncate_to_token_budget(self, text: str, *, runtime: LLMRuntime) -> str:
        """Truncate text so it fits within the consolidation LLM's token budget."""
        budget = self._input_token_budget(runtime)
        if budget <= 0:
            return truncate_text(text, _RAW_ARCHIVE_MAX_CHARS)
        return truncate_text_to_tokens(text, budget)

    async def archive(
        self,
        messages: list[dict[str, Any]],
        *,
        runtime: LLMRuntime | None = None,
        session_key: str | None = None,
        max_chars: int | None = None,
    ) -> str | None:
        """Summarize and archive dropped messages (no-op — consolidation removed).

        Retained as a no-op stub so that internal callers
        (``_consolidate_replay_overflow``) and test mocks do not break.
        """
        logger.warning(
            "archive() called for session {} with {} messages — no-op",
            session_key or "unknown",
            len(messages),
        )
        return None

    async def maybe_consolidate_by_tokens(
        self,
        session: Session,
        *,
        runtime: LLMRuntime,
    ) -> str | None:
        """Token-based consolidation trigger (no-op — consolidation removed).

        Retained as a no-op stub so that test mocks do not break.
        """
        return None

    async def compact_idle_session(
        self,
        session_key: str,
        *,
        runtime: LLMRuntime,
        max_suffix: int | None = None,
    ) -> str | None:
        """Compact an idle session (no-op — consolidation removed).

        Retained as a no-op stub so that test mocks do not break.
        """
        return None
