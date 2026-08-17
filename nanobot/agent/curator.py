"""Curator: automatic skill lifecycle management.

Maintains agent-created skills through a deterministic state machine:
  ACTIVE → STALE → ARCHIVED

Safety principles (from Hermes):
- Only process skills with created_by == "agent"
- Never physically delete, only archive (state = "archived")
- Skip pinned skills and skills referenced by cron jobs
- Support dry-run mode for preview before execution

Trigger: idle-triggered (Hermes-style). After each turn, maybe_run_curator()
checks if enough time has passed since the last run AND the agent has been
idle long enough. If both conditions are met, the Curator runs in the
background. No cron daemon needed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.skill_usage import (
    DEFAULT_ARCHIVE_AFTER_DAYS,
    DEFAULT_STALE_AFTER_DAYS,
    STATE_ACTIVE,
    STATE_ARCHIVED,
    STATE_STALE,
    SkillUsageRecord,
    SkillUsageStore,
)

if TYPE_CHECKING:
    from nanobot.agent.skills import SkillsLoader
    from nanobot.utils.llm_runtime import LLMRuntime

# ---------------------------------------------------------------------------
# Idle-triggered Curator (Hermes-style)
# ---------------------------------------------------------------------------

# Default thresholds for idle-triggered execution
DEFAULT_INTERVAL_HOURS = 24 * 7   # At least 7 days between runs
DEFAULT_MIN_IDLE_HOURS = 0.5      # Agent must be idle for 30 minutes

# Curator state file: tracks last_run_at, run_count, etc.
_CURATOR_STATE_FILE = ".curator_state.json"


def _state_path(workspace: Path) -> Path:
    return workspace / "skills" / _CURATOR_STATE_FILE


def load_curator_state(workspace: Path) -> dict[str, Any]:
    """Load curator state from disk, or return defaults."""
    p = _state_path(workspace)
    if p.exists():
        try:
            data = json.loads(p.read_text("utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "last_run_at": 0.0,
        "run_count": 0,
    }


def save_curator_state(workspace: Path, state: dict[str, Any]) -> None:
    """Atomically save curator state to disk."""
    p = _state_path(workspace)
    tmp = p.with_suffix(".json.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        logger.warning(f"Failed to save curator state: {e}")
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def should_run_curator(
    workspace: Path,
    last_activity_at: float,
    *,
    interval_hours: float = DEFAULT_INTERVAL_HOURS,
    min_idle_hours: float = DEFAULT_MIN_IDLE_HOURS,
) -> bool:
    """Check if the Curator should run now.

    Conditions (both must be true):
    1. At least interval_hours since last run
    2. Agent has been idle for at least min_idle_hours
    """
    state = load_curator_state(workspace)
    now = time.time()

    hours_since_last = (now - state.get("last_run_at", 0)) / 3600
    idle_hours = (now - last_activity_at) / 3600 if last_activity_at > 0 else float("inf")

    return hours_since_last >= interval_hours and idle_hours >= min_idle_hours


def mark_curator_run(workspace: Path) -> None:
    """Update curator state after a successful run."""
    state = load_curator_state(workspace)
    state["last_run_at"] = time.time()
    state["run_count"] = state.get("run_count", 0) + 1
    save_curator_state(workspace, state)

# ---------------------------------------------------------------------------
# LLM umbrella consolidation (Stage 5)
# ---------------------------------------------------------------------------

# Default: OFF — deterministic prune still runs; LLM pass is opt-in.
DEFAULT_CONSOLIDATE = False

CURATOR_REVIEW_PROMPT = """\
You are running as the background skill CURATOR. This is an UMBRELLA-BUILDING
consolidation pass, not a passive audit.

The goal of the skill collection is a LIBRARY OF CLASS-LEVEL INSTRUCTIONS AND
EXPERIENTIAL KNOWLEDGE. A collection of hundreds of narrow skills where each one
captures one session's specific bug is a FAILURE of the library. One broad
umbrella skill with labeled subsections beats five narrow siblings for
discoverability.

The right target shape is CLASS-LEVEL skills with rich SKILL.md bodies — not
one-session-one-skill micro-entries.

Hard rules — do not violate:
1. DO NOT touch builtin skills (source: builtin). Only workspace skills are
   candidates.
2. DO NOT delete any skill. Archiving (skill_manage action=delete) is the
   maximum destructive action.
3. DO NOT touch skills shown as pinned=yes. Skip them entirely.
4. DO NOT use usage counters as a reason to skip consolidation. Judge overlap
   on CONTENT, not on use_count.
5. DO NOT reject consolidation on the grounds that 'each skill has a distinct
   trigger'. The right bar is: 'would a human maintainer write this as N
   separate skills, or as one skill with N labeled subsections?' When the
   answer is the latter, merge.

How to work:
1. Use skills(action='list') to scan the full candidate list.
2. Identify PREFIX CLUSTERS (skills sharing a first word or domain keyword).
3. For each cluster with 2+ members, ask 'what is the UMBRELLA CLASS these
   skills all serve?' If a class exists, consolidate:
   a. MERGE INTO EXISTING UMBRELLA — one skill is already broad enough.
      Patch it to add labeled sections for each sibling, then archive siblings.
   b. CREATE A NEW UMBRELLA — no existing member is broad enough. Create a
      new class-level skill, then archive the narrow siblings.
4. Use skills(action='view', name=...) to read skill content before merging.
5. Use skill_manage(action='patch') to add sections to the umbrella.
6. Use skill_manage(action='create') to create new umbrella skills.
7. Use skill_manage(action='delete') to archive absorbed siblings.
8. Iterate — don't stop after one merge. Process every obvious cluster.

When done, write a human-readable summary of what you consolidated and why.
If no consolidation opportunities exist, say so briefly.
"""


def _render_candidate_list(skills_loader: SkillsLoader, usage_store: SkillUsageStore) -> str:
    """Build a text listing of all curator-managed candidate skills."""
    all_skills = skills_loader.list_skills(filter_unavailable=False)
    records = {rec.name: rec for rec in usage_store.list_all()}

    lines: list[str] = []
    for entry in all_skills:
        name = entry["name"]
        source = entry.get("source", "")
        # Only workspace skills are candidates (skip builtin)
        if source != "workspace":
            continue
        rec = records.get(name)
        # Only agent-created, non-pinned skills
        if rec is None or rec.created_by != "agent" or rec.pinned:
            continue
        desc = skills_loader.get_skill_description(name)
        state = rec.state
        use_count = rec.use_count
        lines.append(f"- {name} (state={state}, use={use_count}) — {desc}")

    if not lines:
        return "No agent-created workspace skills found."
    return "## Candidate Skills\n\n" + "\n".join(lines)


async def run_llm_consolidation(
    workspace: Path,
    skills_loader: SkillsLoader,
    usage_store: SkillUsageStore,
    runtime: LLMRuntime,
    *,
    dry_run: bool = False,
    max_iterations: int = 30,
) -> dict[str, Any]:
    """Run LLM-driven umbrella consolidation on agent-created skills.

    Forks a lightweight agent (like BackgroundReviewer) with skill tools.
    The agent reads the skill landscape, identifies overlapping clusters,
    and merges them into broader umbrella skills.

    Args:
        workspace: Workspace path.
        skills_loader: SkillsLoader instance.
        usage_store: SkillUsageStore instance.
        runtime: LLM runtime to use for the fork agent.
        dry_run: If True, instruct the agent to only report, not mutate.
        max_iterations: Max tool-call iterations for the fork agent.

    Returns:
        Dict with 'summary' (str), 'tools_used' (list[str]), 'error' (str|None).
    """
    from nanobot.agent.runner import AgentRunner, AgentRunSpec
    from nanobot.agent.skill_provenance import (
        BACKGROUND_REVIEW,
        reset_background_review_read_marks,
        set_current_write_origin,
        reset_current_write_origin,
    )
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.agent.tools.skill_manager import SkillManagerTool
    from nanobot.agent.tools.skills_tool import SkillsTool

    candidate_list = _render_candidate_list(skills_loader, usage_store)
    if "No agent-created" in candidate_list:
        return {
            "summary": "skipped (no candidates)",
            "tools_used": [],
            "error": None,
        }

    # Build prompt
    prompt_parts = [CURATOR_REVIEW_PROMPT]
    if dry_run:
        prompt_parts.append(
            "\n\nDRY-RUN MODE: Do NOT call skill_manage with create, patch, or delete. "
            "Only use skills(action='list') and skills(action='view') to read. "
            "Report what you WOULD do, not what you did."
        )
    prompt_parts.append(f"\n\n{candidate_list}")
    full_prompt = "\n".join(prompt_parts)

    # Build fork agent messages
    fork_messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are a background skill curator. Your task is to consolidate "
                "overlapping skills into broader umbrella skills for better "
                "discoverability. Follow the instructions carefully."
            ),
        },
        {
            "role": "user",
            "content": full_prompt,
        },
    ]

    # Build restricted tool registry (skills read + skill_manage write)
    tools = ToolRegistry()
    skills_tool = SkillsTool(
        skills_loader=skills_loader,
        usage_store=usage_store,
    )
    tools.register(skills_tool)

    if not dry_run:
        skill_manager = SkillManagerTool(
            workspace=workspace,
            usage_store=usage_store,
        )
        tools.register(skill_manager)

    # Set provenance: mark as background_review origin
    reset_background_review_read_marks()
    origin_token = set_current_write_origin(BACKGROUND_REVIEW)

    runner = AgentRunner()
    result_meta: dict[str, Any] = {
        "summary": "",
        "tools_used": [],
        "error": None,
    }

    try:
        logger.info("Starting LLM consolidation pass")
        result = await runner.run(
            AgentRunSpec(
                initial_messages=fork_messages,
                tools=tools,
                runtime=runtime,
                max_iterations=max_iterations,
                max_tool_result_chars=8000,
                error_message=None,
            )
        )

        result_meta["tools_used"] = result.tools_used or []
        # Extract summary from the final assistant message
        if result.messages:
            for msg in reversed(result.messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    content = msg["content"]
                    if isinstance(content, str):
                        result_meta["summary"] = content[:500]
                        break

        logger.info(
            "LLM consolidation completed: {} tool(s) used ({})",
            len(result_meta["tools_used"]),
            ", ".join(result_meta["tools_used"]) if result_meta["tools_used"] else "none",
        )
    except Exception as e:
        logger.warning("LLM consolidation failed: {}", e)
        result_meta["error"] = str(e)
        result_meta["summary"] = f"error ({e})"
    finally:
        reset_current_write_origin(origin_token)
        reset_background_review_read_marks()

    return result_meta


# Default thresholds for dynamic always promotion/demotion
DEFAULT_PROMOTE_USE_COUNT = 5       # Promote to dynamic_always after N uses
DEFAULT_DEMOTE_IDLE_DAYS = 3        # Demote dynamic_always if idle for N days


@dataclass
class CuratorConfig:
    """Configuration for the Curator."""

    stale_after_days: float = DEFAULT_STALE_AFTER_DAYS
    archive_after_days: float = DEFAULT_ARCHIVE_AFTER_DAYS
    promote_use_count: int = DEFAULT_PROMOTE_USE_COUNT
    demote_idle_days: float = DEFAULT_DEMOTE_IDLE_DAYS
    dry_run: bool = False
    consolidate: bool = DEFAULT_CONSOLIDATE


def apply_automatic_transitions(
    records: list[SkillUsageRecord],
    now: float,
    config: CuratorConfig,
) -> list[SkillUsageRecord]:
    """Pure function: apply deterministic state transitions.

    Rules:
    - ACTIVE → STALE if idle > stale_after_days
    - STALE → ARCHIVED if idle > archive_after_days
    - Never transition pinned or builtin skills
    - Never transition user-created skills (only agent-created)

    Args:
        records: List of skill usage records to process.
        now: Current timestamp (time.time()).
        config: Curator configuration.

    Returns:
        Updated list of records (mutated in place).
    """
    out = []
    for rec in records:
        # Skip protected skills
        if rec.pinned or rec.created_by != "agent":
            out.append(rec)
            continue

        idle_days = (now - rec.last_activity_at) / 86400 if rec.last_activity_at > 0 else float("inf")

        if rec.state == STATE_ACTIVE and idle_days > config.stale_after_days:
            if not config.dry_run:
                rec.state = STATE_STALE
            logger.info(
                f"Curator: {rec.name} ACTIVE → STALE (idle {idle_days:.1f} days)"
                + (" [DRY RUN]" if config.dry_run else "")
            )
        elif rec.state == STATE_STALE and idle_days > config.archive_after_days:
            if not config.dry_run:
                rec.state = STATE_ARCHIVED
            logger.info(
                f"Curator: {rec.name} STALE → ARCHIVED (idle {idle_days:.1f} days)"
                + (" [DRY RUN]" if config.dry_run else "")
            )

        out.append(rec)
    return out


def apply_dynamic_always(
    records: list[SkillUsageRecord],
    now: float,
    config: CuratorConfig,
) -> list[SkillUsageRecord]:
    """Promote or demote skills based on usage patterns.

    Promotion: ACTIVE + use_count >= promote_use_count → dynamic_always=True
    Demotion: dynamic_always + idle > demote_idle_days → dynamic_always=False

    Rules:
    - Only process agent-created, non-pinned skills
    - Never touch skills with static always:true in frontmatter
      (those are managed by the user, not the Curator)
    """
    out = []
    for rec in records:
        if rec.pinned or rec.created_by != "agent":
            out.append(rec)
            continue

        idle_days = (now - rec.last_activity_at) / 86400 if rec.last_activity_at > 0 else float("inf")

        if not rec.dynamic_always:
            # Promotion: high usage → auto-promote to always
            if (
                rec.state == STATE_ACTIVE
                and rec.use_count >= config.promote_use_count
            ):
                if not config.dry_run:
                    rec.dynamic_always = True
                logger.info(
                    f"Curator: {rec.name} promoted to dynamic_always "
                    f"(use_count={rec.use_count})"
                    + (" [DRY RUN]" if config.dry_run else "")
                )
        else:
            # Demotion: idle too long → remove dynamic_always
            if idle_days > config.demote_idle_days:
                if not config.dry_run:
                    rec.dynamic_always = False
                logger.info(
                    f"Curator: {rec.name} demoted from dynamic_always "
                    f"(idle {idle_days:.1f} days)"
                    + (" [DRY RUN]" if config.dry_run else "")
                )

        out.append(rec)
    return out


class Curator:
    """Skill lifecycle manager with deterministic state transitions."""

    def __init__(
        self,
        usage_store: SkillUsageStore,
        skills_loader: SkillsLoader,
        config: CuratorConfig | None = None,
    ):
        self.usage_store = usage_store
        self.skills_loader = skills_loader
        self.config = config or CuratorConfig()

    async def run(self, dry_run: bool = False, runtime: LLMRuntime | None = None) -> dict:
        """Run the curator: apply state transitions and optionally LLM consolidation.

        Args:
            dry_run: If True, preview changes without saving.
            runtime: LLM runtime for consolidation pass (required if consolidate=True).

        Returns:
            Dict with summary of changes.
        """
        config = CuratorConfig(
            stale_after_days=self.config.stale_after_days,
            archive_after_days=self.config.archive_after_days,
            promote_use_count=self.config.promote_use_count,
            demote_idle_days=self.config.demote_idle_days,
            dry_run=dry_run,
            consolidate=self.config.consolidate,
        )

        records = self.usage_store.list_all()
        now = time.time()

        # Count state changes
        before = {rec.name: rec.state for rec in records}
        updated = apply_automatic_transitions(records, now, config)
        after = {rec.name: rec.state for rec in updated}

        # Apply dynamic always promotion/demotion
        before_always = {rec.name: rec.dynamic_always for rec in updated}
        updated = apply_dynamic_always(updated, now, config)
        after_always = {rec.name: rec.dynamic_always for rec in updated}

        # Save changes (unless dry_run)
        if not dry_run:
            for rec in updated:
                state_changed = before.get(rec.name) != after.get(rec.name)
                always_changed = before_always.get(rec.name) != after_always.get(rec.name)
                if state_changed or always_changed:
                    self.usage_store.save(rec)

        # Build summary
        transitions = []
        for name, new_state in after.items():
            old_state = before.get(name)
            if old_state != new_state:
                transitions.append({
                    "name": name,
                    "from": old_state,
                    "to": new_state,
                })

        # Build dynamic always changes
        always_changes = []
        for name, new_val in after_always.items():
            old_val = before_always.get(name, False)
            if old_val != new_val:
                always_changes.append({
                    "name": name,
                    "action": "promoted" if new_val else "demoted",
                })

        result = {
            "total_skills": len(records),
            "transitions": transitions,
            "dynamic_always_changes": always_changes,
            "dry_run": dry_run,
        }

        # LLM consolidation pass (opt-in, requires runtime)
        if config.consolidate and runtime is not None:
            try:
                llm_result = await run_llm_consolidation(
                    workspace=self.usage_store.workspace,
                    skills_loader=self.skills_loader,
                    usage_store=self.usage_store,
                    runtime=runtime,
                    dry_run=dry_run,
                )
                result["llm_consolidation"] = llm_result
            except Exception as e:
                logger.warning("LLM consolidation failed: {}", e)
                result["llm_consolidation"] = {
                    "summary": f"error ({e})",
                    "tools_used": [],
                    "error": str(e),
                }

        return result

    def get_archived_skills(self) -> list[str]:
        """Get list of archived skill names."""
        records = self.usage_store.list_all()
        return [rec.name for rec in records if rec.state == STATE_ARCHIVED]

    def revive_skill(self, name: str) -> bool:
        """Manually revive an archived or stale skill to ACTIVE.

        Args:
            name: Skill name to revive.

        Returns:
            True if revived, False if not found or already active.
        """
        rec = self.usage_store.load(name)
        if rec.state == STATE_ACTIVE:
            return False

        rec.state = STATE_ACTIVE
        rec.last_activity_at = time.time()
        self.usage_store.save(rec)
        logger.info(f"Curator: manually revived {name} to ACTIVE")
        return True
