"""Hook that enforces tool-level permission checks before tool execution."""

from __future__ import annotations

from typing import Any

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.providers.base import ToolCallRequest
from nanobot.security.tool_permissions import ToolPermissionManager


class ToolPermissionDenied(Exception):
    """Raised when a tool call is denied by the permission manager."""

    def __init__(self, tool_name: str, resource: str, action: str, note: str | None = None):
        self.tool_name = tool_name
        self.resource = resource
        self.action = action
        self.note = note
        msg = f"Permission denied: {tool_name} cannot {action} on {resource}"
        if note:
            msg += f" ({note})"
        super().__init__(msg)


class ToolPermissionHook(AgentHook):
    """Check tool permissions before execution.

    Integrates ToolPermissionManager into the agent execution chain by
    checking permissions in before_execute_tool. If a tool call is denied,
    raises ToolPermissionDenied which the runner will catch and return
    as an error result to the LLM.
    """

    def __init__(self, permission_manager: ToolPermissionManager) -> None:
        super().__init__()
        self._manager = permission_manager

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
    ) -> None:
        """Check permissions before tool execution.

        Extracts resource identifiers from tool parameters and checks
        if the tool has permission to operate on them.
        """
        tool_name = tool_call.name
        arguments = tool_call.arguments or {}

        # Extract resource paths from common parameter names
        resource = self._extract_resource(arguments)
        if resource is None:
            # No resource to check — allow by default
            return

        # Determine action from tool name
        action = self._infer_action(tool_name)

        # Check permission
        if not self._manager.check(tool_name, resource, action):
            note = self._manager.get_note(tool_name, resource)
            logger.warning(
                f"Tool permission denied: {tool_name} -> {resource} ({action})"
            )
            raise ToolPermissionDenied(tool_name, resource, action, note)

        logger.debug(f"Tool permission granted: {tool_name} -> {resource} ({action})")

    @staticmethod
    def _extract_resource(arguments: dict[str, Any]) -> str | None:
        """Extract a resource identifier from tool arguments.

        Looks for common parameter names that represent file paths,
        database names, or other resource identifiers.
        """
        for key in ("path", "file_path", "filepath", "file", "database", "db_path", "resource"):
            value = arguments.get(key)
            if value and isinstance(value, str):
                return value
        return None

    @staticmethod
    def _infer_action(tool_name: str) -> str:
        """Infer the action type from the tool name."""
        name_lower = tool_name.lower()
        if any(kw in name_lower for kw in ("read", "list", "get", "fetch", "query", "analyze")):
            return "read"
        if any(kw in name_lower for kw in ("write", "edit", "create", "update", "delete", "remove")):
            return "write"
        if any(kw in name_lower for kw in ("exec", "run", "shell", "bash")):
            return "execute"
        return "read"  # Default to read for unknown tools
