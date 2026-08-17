"""Skills tool: list and view skills for the LLM.

Provides read-only access to skills for the agent, allowing it to discover
and inspect available skills without modifying them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.agent.skill_provenance import mark_background_review_skill_read
from nanobot.agent.skills import SkillsLoader
from nanobot.agent.skill_usage import SkillUsageStore
from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.config_base import Base


class SkillsToolConfig(Base):
    """Configuration for the skills tool."""

    enable: bool = True


class SkillsTool(Tool):
    """Tool for listing and viewing skills."""

    config_key = "skills"
    _plugin_discoverable = False  # Manual registration (needs SkillsLoader + SkillUsageStore)

    @classmethod
    def config_cls(cls):
        return SkillsToolConfig

    def __init__(
        self,
        skills_loader: SkillsLoader,
        usage_store: SkillUsageStore,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.skills_loader = skills_loader
        self.usage_store = usage_store

    @property
    def name(self) -> str:
        return "skills"

    @property
    def description(self) -> str:
        return (
            "List or view available skills. Skills are reusable procedures that can be "
            "invoked with $skill-name syntax. Use 'list' to see all available skills, "
            "or 'view' to read a specific skill's content."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "view"],
                    "description": "Action to perform: 'list' all skills or 'view' a specific skill.",
                },
                "name": {
                    "type": "string",
                    "description": "Skill name (required for 'view' action).",
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action", "")
        name = kwargs.get("name", "")

        if action == "list":
            return self._list_skills()
        elif action == "view":
            if not name:
                return ToolResult(
                    "Error: 'name' is required for 'view' action.",
                    is_error=True,
                )
            return self._view_skill(name)
        else:
            return ToolResult(f"Error: Unknown action '{action}'.", is_error=True)

    def _list_skills(self) -> ToolResult:
        """List all available skills with metadata."""
        skills = self.skills_loader.list_skills(filter_unavailable=True)
        if not skills:
            return ToolResult("No skills available.")

        lines = ["Available skills:\n"]
        for entry in skills:
            skill_name = entry["name"]
            source = entry["source"]
            
            # Get description
            desc = self.skills_loader.get_skill_description(skill_name)
            
            # Get usage stats
            try:
                rec = self.usage_store.load(skill_name)
                use_count = rec.use_count
                state = rec.state
                stats = f" (uses: {use_count}, state: {state})"
            except Exception:
                stats = ""
            
            lines.append(f"- **{skill_name}** [{source}]{stats}: {desc}")

        return ToolResult("\n".join(lines))

    def _view_skill(self, name: str) -> ToolResult:
        """View the content of a specific skill."""
        content = self.skills_loader.load_skill(name)
        if not content:
            return ToolResult(f"Error: Skill '{name}' not found.", is_error=True)

        # Record view event
        try:
            self.usage_store.record_view(name)
        except Exception:
            pass  # Best-effort tracking

        # Mark as read for background review read-before-write guard
        try:
            skill_file = self.skills_loader.workspace_skills / name / "SKILL.md"
            if not skill_file.exists():
                # Try builtin skills dir
                if self.skills_loader.builtin_skills:
                    skill_file = self.skills_loader.builtin_skills / name / "SKILL.md"
            mark_background_review_skill_read(skill_file)
        except Exception:
            pass  # Best-effort tracking

        return ToolResult(content)
