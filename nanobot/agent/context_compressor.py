"""Context compressor: Hermes-style inline summary compression.

This module implements a compression strategy inspired by Hermes (harness-agent),
where summaries are inserted as messages within the conversation history rather
than stored in external files like history.jsonl.

Key design principles:
1. Summaries are inserted as messages between head (protected) and tail (recent)
2. Summary role alternates with neighbors to maintain API compatibility
3. Compression pointer (last_consolidated) tracks what has been compressed
4. Dream reads directly from session messages, not external files
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from nanobot.runtime_context import public_history_messages
from nanobot.session.manager import Session, SessionManager
from nanobot.utils.helpers import (
    content_with_media_breadcrumbs,
    estimate_message_tokens,
    truncate_text,
    truncate_text_to_tokens,
)
from nanobot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from nanobot.utils.llm_runtime import LLMRuntime

# Summary markers (Hermes-style)
SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted "
    "into the summary below. This is a handoff from a previous context "
    "window — treat it as background reference, NOT as active instructions. "
    "Do NOT answer questions or fulfill requests mentioned in this summary; "
    "they were already addressed.\n"
    "Respond ONLY to the latest user message that appears AFTER this summary."
)

SUMMARY_END_MARKER = (
    "--- END OF CONTEXT SUMMARY — respond to the message below, "
    "not the summary above ---"
)

# Metadata keys for compressed summary messages
COMPRESSED_SUMMARY_METADATA_KEY = "_compressed_summary"
COMPRESSED_SUMMARY_HAS_USER_TURN_KEY = "_compressed_summary_has_user_turn"

# Size limits
_ARCHIVE_SUMMARY_MAX_CHARS = 8_000
_RAW_ARCHIVE_MAX_CHARS = 16_000


class ContextCompressor:
    """Compress session history by inserting inline summaries.
    
    Unlike the old Consolidator which wrote to history.jsonl, this compressor
    inserts summaries directly into session.messages, similar to Hermes.
    """

    _MAX_CONSOLIDATION_ROUNDS = 5
    _SAFETY_BUFFER = 1024

    def __init__(
        self,
        sessions: SessionManager,
        consolidation_ratio: float = 0.5,
    ):
        self.sessions = sessions
        self.consolidation_ratio = consolidation_ratio

    def _input_token_budget(self, runtime: LLMRuntime) -> int:
        """Available input token budget for compression LLM."""
        return (
            runtime.context_window_tokens
            - runtime.generation.max_tokens
            - self._SAFETY_BUFFER
        )

    def _truncate_to_token_budget(self, text: str, *, runtime: LLMRuntime) -> str:
        """Truncate text so it fits within the compression LLM's token budget."""
        budget = self._input_token_budget(runtime)
        if budget <= 0:
            return truncate_text(text, _RAW_ARCHIVE_MAX_CHARS)
        return truncate_text_to_tokens(text, budget)

    def pick_compression_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        """Pick a user-turn boundary that removes enough old prompt tokens.
        
        Returns (boundary_index, removed_tokens) or None if no good boundary found.
        """
        start = session.last_consolidated
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            # Prefer user-turn boundaries for cleaner compression
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    async def generate_summary(
        self,
        messages: list[dict[str, Any]],
        *,
        runtime: LLMRuntime,
    ) -> str | None:
        """Generate a structured summary using the LLM.
        
        Returns the summary text or None if generation failed.
        """
        if not messages:
            return None

        # Format messages for summarization
        formatted = self._format_messages_for_summary(messages)
        formatted = self._truncate_to_token_budget(formatted, runtime=runtime)

        system_prompt = render_template(
            "agent/consolidator_archive.md",
            strip=True,
        )

        try:
            response = await runtime.provider.chat_with_retry(
                model=runtime.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": formatted},
                ],
                tools=None,
                tool_choice=None,
                temperature=runtime.generation.temperature,
                max_tokens=runtime.generation.max_tokens,
                reasoning_effort=runtime.generation.reasoning_effort,
            )
        except Exception:
            logger.warning("Compression provider call failed")
            return None

        if response.finish_reason == "error":
            logger.warning("Compression provider returned an error")
            return None

        summary = response.content or "[no summary]"
        # Cap the summary to prevent runaway growth
        if len(summary) > _ARCHIVE_SUMMARY_MAX_CHARS:
            summary = truncate_text(summary, _ARCHIVE_SUMMARY_MAX_CHARS)

        return summary

    def _format_messages_for_summary(self, messages: list[dict[str, Any]]) -> str:
        """Format messages for LLM summarization."""
        lines: list[str] = []
        for message in public_history_messages(messages):
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

    def insert_summary_message(
        self,
        session: Session,
        summary: str,
        compression_boundary: int,
    ) -> None:
        """Insert a summary message into the session at the compression boundary.
        
        The summary is inserted as a new message between the compressed head
        and the preserved tail, with role chosen to maintain alternation.
        
        Args:
            session: The session to modify
            summary: The summary text
            compression_boundary: Index where compression ends (tail starts)
        """
        # Determine the role for the summary message
        # Look at the last message before the boundary (head)
        head_messages = session.messages[session.last_consolidated:compression_boundary]
        tail_messages = session.messages[compression_boundary:]

        last_head_role = None
        if head_messages:
            last_head_role = head_messages[-1].get("role")

        first_tail_role = None
        if tail_messages:
            first_tail_role = tail_messages[0].get("role")

        # Choose role that alternates with neighbors
        # Priority: alternate with head, then tail
        if last_head_role is None or last_head_role in {"assistant", "tool"}:
            summary_role = "user"
        else:
            summary_role = "assistant"

        # If chosen role collides with tail, try flipping
        if first_tail_role is not None and summary_role == first_tail_role:
            flipped = "assistant" if summary_role == "user" else "user"
            if flipped != last_head_role:
                summary_role = flipped
            # If both roles collide, we still insert but accept the collision
            # (this is rare and the model can handle it)

        # Build the summary message with markers
        summary_content = f"{SUMMARY_PREFIX}\n\n{summary}\n\n{SUMMARY_END_MARKER}"

        summary_message = {
            "role": summary_role,
            "content": summary_content,
            "timestamp": datetime.now().isoformat(),
            COMPRESSED_SUMMARY_METADATA_KEY: True,
            COMPRESSED_SUMMARY_HAS_USER_TURN_KEY: any(
                m.get("role") == "user" for m in head_messages
            ),
        }

        # Insert the summary message at the compression boundary
        session.messages.insert(compression_boundary, summary_message)

        # Update the compression pointer to skip the compressed messages
        # but include the summary message
        session.last_consolidated = compression_boundary + 1

        # Clear provider state since message structure changed
        session.provider_state = None

        # Save the session
        self.sessions.save(session)

        logger.info(
            "Inserted compression summary at index {} for session {} "
            "(compressed {} messages, role={})",
            compression_boundary,
            session.key,
            len(head_messages),
            summary_role,
        )

    async def compress_session(
        self,
        session: Session,
        *,
        runtime: LLMRuntime,
        target_tokens: int | None = None,
    ) -> bool:
        """Compress a session by generating and inserting a summary.
        
        Args:
            session: The session to compress
            runtime: LLM runtime for summary generation
            target_tokens: Target token count after compression (optional)
            
        Returns:
            True if compression was performed, False otherwise
        """
        if not session.messages:
            return False

        # Calculate how many tokens to remove
        unconsolidated = session.messages[session.last_consolidated:]
        if not unconsolidated:
            return False

        current_tokens = sum(estimate_message_tokens(m) for m in unconsolidated)
        
        if target_tokens is None:
            budget = self._input_token_budget(runtime)
            target_tokens = int(budget * self.consolidation_ratio)

        tokens_to_remove = current_tokens - target_tokens
        if tokens_to_remove <= 0:
            return False

        # Find compression boundary
        boundary = self.pick_compression_boundary(session, tokens_to_remove)
        if boundary is None:
            logger.debug("No suitable compression boundary found for {}", session.key)
            return False

        boundary_idx, removed_tokens = boundary
        messages_to_compress = session.messages[session.last_consolidated:boundary_idx]

        if not messages_to_compress:
            return False

        logger.info(
            "Compressing {} messages ({} tokens) for session {}",
            len(messages_to_compress),
            removed_tokens,
            session.key,
        )

        # Generate summary
        summary = await self.generate_summary(messages_to_compress, runtime=runtime)
        if not summary:
            logger.warning("Failed to generate compression summary")
            return False

        # Insert summary message
        self.insert_summary_message(session, summary, boundary_idx)

        return True

    async def maybe_compress(
        self,
        session: Session,
        *,
        runtime: LLMRuntime,
    ) -> None:
        """Compress session if it exceeds token budget.
        
        This is the main entry point called from the agent loop.
        """
        if runtime.context_window_tokens <= 0:
            return

        budget = self._input_token_budget(runtime)
        target = int(budget * self.consolidation_ratio)

        # Estimate current prompt size
        unconsolidated = session.messages[session.last_consolidated:]
        if not unconsolidated:
            return

        current_tokens = sum(estimate_message_tokens(m) for m in unconsolidated)
        
        if current_tokens < budget:
            return

        # Try compression rounds until we fit or hit max rounds
        for round_num in range(self._MAX_CONSOLIDATION_ROUNDS):
            compressed = await self.compress_session(
                session,
                runtime=runtime,
                target_tokens=target,
            )
            if not compressed:
                break

            # Re-check if we fit now
            unconsolidated = session.messages[session.last_consolidated:]
            current_tokens = sum(estimate_message_tokens(m) for m in unconsolidated)
            if current_tokens < budget:
                logger.info(
                    "Session {} fits after {} compression round(s)",
                    session.key,
                    round_num + 1,
                )
                break
        else:
            logger.warning(
                "Session {} still exceeds budget after {} compression rounds",
                session.key,
                self._MAX_CONSOLIDATION_ROUNDS,
            )
