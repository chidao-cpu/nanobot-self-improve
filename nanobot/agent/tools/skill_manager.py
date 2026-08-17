"""Skill manager tool: create, patch, delete agent skills.

Allows the LLM to create and manage skills (SKILL.md files) in the workspace.
Skills are stored in workspace/skills/<name>/SKILL.md with YAML frontmatter.

Security: all writes are restricted to workspace/skills/ to prevent path traversal.
Background review writes are further restricted to agent-created skills only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.skill_provenance import (
    background_review_has_read,
    is_background_review,
)
from nanobot.agent.skill_usage import SkillUsageStore
from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.config_base import Base

# Validate skill names: alphanumeric, hyphens, underscores only
_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Detect YAML frontmatter at the start of a skill file
_FRONTMATTER_RE = re.compile(r"^---\s*\r?\n.*?\r?\n---\s*\r?\n?", re.DOTALL)


def _ensure_frontmatter(name: str, content: str) -> str:
    """Ensure skill content has YAML frontmatter with name and description.

    If content already starts with valid frontmatter, return as-is.
    Otherwise, prepend a minimal frontmatter block.
    """
    if _FRONTMATTER_RE.match(content):
        return content
    # Generate a human-readable description from the skill name
    desc = name.replace("-", " ").replace("_", " ").title()
    frontmatter = (
        f"---\n"
        f"name: {name}\n"
        f"description: {desc}\n"
        f"---\n\n"
    )
    return frontmatter + content


class SkillManagerConfig(Base):
    """Configuration for the skill_manager tool."""

    enable: bool = True


class SkillManagerTool(Tool):
    """Tool for creating and managing agent skills."""

    config_key = "skill_manager"
    _plugin_discoverable = False  # Manual registration (needs workspace + usage_store)

    @classmethod
    def config_cls(cls):
        return SkillManagerConfig

    def __init__(
        self,
        workspace: Path,
        usage_store: SkillUsageStore,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.workspace = workspace
        self.skills_dir = workspace / "skills"
        self.usage_store = usage_store

    @property
    def name(self) -> str:
        return "skill_manage"

    @property
    def description(self) -> str:
        return (
            "Create, patch, or delete an agent skill (SKILL.md). "
            "Skills are reusable procedures stored in workspace/skills/<name>/SKILL.md. "
            "Use this to persist durable, reusable knowledge that can be invoked "
            "across sessions with $skill-name syntax."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "patch", "delete"],
                    "description": "The action to perform.",
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Skill name (alphanumeric, hyphens, underscores). "
                        "Used as directory name under workspace/skills/."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Skill content (Markdown with optional YAML frontmatter). "
                        "Required for 'create' and 'patch'."
                    ),
                },
            },
            "required": ["action", "name"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action", "")
        name = kwargs.get("name", "")
        content = kwargs.get("content", "")

        # Validate skill name
        if not name or not _SKILL_NAME_RE.match(name):
            return ToolResult(
                f"Error: Invalid skill name '{name}'. "
                "Use alphanumeric characters, hyphens, and underscores only.",
                is_error=True,
            )

        # Security: ensure skill directory is within workspace/skills/
        skill_dir = (self.skills_dir / name).resolve()
        if not str(skill_dir).startswith(str(self.skills_dir.resolve())):
            return ToolResult(
                f"Error: Path traversal detected. Skill name '{name}' is not allowed.",
                is_error=True,
            )

        # Background review guards: restrict autonomous writes
        if is_background_review():
            guard_result = self._background_review_write_guard(name, action, skill_dir)
            if guard_result is not None:
                return guard_result
            guard_result = self._background_review_read_before_write_guard(name, skill_dir, action)
            if guard_result is not None:
                return guard_result

        if action == "create":
            return await self._create_skill(name, skill_dir, content)
        elif action == "patch":
            return await self._patch_skill(name, skill_dir, content)
        elif action == "delete":
            return await self._delete_skill(name, skill_dir)
        else:
            return ToolResult(f"Error: Unknown action '{action}'.", is_error=True)

    async def _create_skill(
        self, name: str, skill_dir: Path, content: str
    ) -> ToolResult:
        """Create a new skill."""
        if not content:
            return ToolResult(
                "Error: 'content' is required for 'create' action.",
                is_error=True,
            )

        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists():
            return ToolResult(
                f"Error: Skill '{name}' already exists. Use 'patch' to update it.",
                is_error=True,
            )

        # Ensure YAML frontmatter is present
        content = _ensure_frontmatter(name, content)

        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(content, encoding="utf-8")
            # Mark as agent-created in usage store
            self.usage_store.mark_created(name, created_by="agent")
            logger.info(f"Created skill: {name}")
            return ToolResult(f"Skill '{name}' created successfully.")
        except OSError as e:
            logger.error(f"Failed to create skill {name}: {e}")
            return ToolResult(f"Error: Failed to create skill: {e}", is_error=True)

    async def _patch_skill(
        self, name: str, skill_dir: Path, content: str
    ) -> ToolResult:
        """Update an existing skill."""
        if not content:
            return ToolResult(
                "Error: 'content' is required for 'patch' action.",
                is_error=True,
            )

        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            return ToolResult(
                f"Error: Skill '{name}' does not exist. Use 'create' to create it.",
                is_error=True,
            )

        # Ensure YAML frontmatter is present
        content = _ensure_frontmatter(name, content)

        try:
            skill_file.write_text(content, encoding="utf-8")
            # Update activity timestamp
            self.usage_store.record_use(name)
            logger.info(f"Patched skill: {name}")
            return ToolResult(f"Skill '{name}' updated successfully.")
        except OSError as e:
            logger.error(f"Failed to patch skill {name}: {e}")
            return ToolResult(f"Error: Failed to update skill: {e}", is_error=True)

    async def _delete_skill(self, name: str, skill_dir: Path) -> ToolResult:
        """Delete a skill (archive in usage store, remove files)."""
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            return ToolResult(
                f"Error: Skill '{name}' does not exist.",
                is_error=True,
            )

        # Check if protected
        if self.usage_store.is_protected(name):
            return ToolResult(
                f"Error: Skill '{name}' is protected (pinned or builtin) and cannot be deleted.",
                is_error=True,
            )

        try:
            # Remove skill directory
            import shutil
            shutil.rmtree(skill_dir)
            # Mark as archived in usage store
            rec = self.usage_store.load(name)
            rec.state = "archived"
            self.usage_store.save(rec)
            logger.info(f"Deleted skill: {name}")
            return ToolResult(f"Skill '{name}' deleted successfully.")
        except OSError as e:
            logger.error(f"Failed to delete skill {name}: {e}")
            return ToolResult(f"Error: Failed to delete skill: {e}", is_error=True)

    # ── Background review guards ──────────────────────────────────────

    def _background_review_write_guard(
        self, name: str, action: str, skill_dir: Path
    ) -> ToolResult | None:
        """Restrict background review writes to agent-created skills only.

        Background review forks may only:
        - Create new skills (always allowed — they become agent-created)
        - Patch/delete skills that were created by the agent (created_by == "agent")

        Protected from background review:
        - Builtin skills (not in workspace/skills/)
        - Pinned skills
        - User-created skills (created_by == "user")
        - Hub-installed or external-dir skills
        """
        # Create is always allowed — new skills are agent-created by definition
        if action == "create":
            return None

        # Check if skill is protected (pinned or builtin)
        if self.usage_store.is_protected(name):
            logger.info(
                "Background review blocked: skill '{}' is protected (pinned/builtin)",
                name,
            )
            return ToolResult(
                f"Error: Skill '{name}' is protected and cannot be modified during "
                "background review. Only agent-created skills can be modified autonomously.",
                is_error=True,
            )

        # Check if skill was created by the agent
        if not self.usage_store.is_agent_created(name):
            logger.info(
                "Background review blocked: skill '{}' was not created by agent",
                name,
            )
            return ToolResult(
                f"Error: Skill '{name}' was not created by the agent and cannot be "
                "modified during background review. Use the skills tool to view it, "
                "then suggest changes for the user to approve.",
                is_error=True,
            )

        return None

    def _background_review_read_before_write_guard(
        self, name: str, skill_dir: Path, action: str
    ) -> ToolResult | None:
        """Require the background review fork to read a skill before modifying it.

        This ensures the fork works with actual skill content rather than
        inferred knowledge. The fork must call skills(action='view', name=...)
        before calling skill_manage(action='patch'/'delete', name=...).

        Create is exempt — there is no existing content to read.
        """
        if action == "create":
            return None

        skill_file = skill_dir / "SKILL.md"
        if not background_review_has_read(skill_file):
            logger.info(
                "Background review blocked: skill '{}' was not read before write",
                name,
            )
            return ToolResult(
                f"Error: You must read skill '{name}' with skills(action='view', "
                f"name='{name}') before modifying it during background review.",
                is_error=True,
            )

        return None
