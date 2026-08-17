"""Hook that enforces file whitelist/blacklist access control."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.providers.base import ToolCallRequest
from nanobot.security.file_whitelist import FileAccessControl


class FileAccessDenied(Exception):
    """Raised when file access is denied by whitelist/blacklist."""

    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"File access denied: {reason}")


class FileAccessHook(AgentHook):
    """Check file access control before tool execution.

    Integrates FileAccessControl (whitelist/blacklist) into the agent execution
    chain by checking file paths in before_execute_tool. If access is denied,
    raises FileAccessDenied which the runner will catch and return as an error
    result to the LLM.
    """

    def __init__(self, file_access_control: FileAccessControl) -> None:
        super().__init__()
        self._controller = file_access_control

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
    ) -> None:
        """Check file access before tool execution.

        Extracts file paths from tool parameters and checks them against
        the whitelist/blacklist.
        """
        tool_name = tool_call.name
        arguments = tool_call.arguments or {}

        # Extract file paths from common parameter names
        paths = self._extract_paths(arguments)
        if not paths:
            # No paths to check — allow by default
            return

        # Check each path
        for path_str in paths:
            allowed, reason = self._controller.check_access(path_str)
            if not allowed:
                logger.warning(
                    f"File access denied: {tool_name} -> {path_str} ({reason})"
                )
                raise FileAccessDenied(path_str, reason or "access denied")

        logger.debug(f"File access granted: {tool_name} -> {paths}")

    @staticmethod
    def _extract_paths(arguments: dict[str, Any]) -> list[str]:
        """Extract file paths from tool arguments.

        Looks for common parameter names that represent file paths.
        """
        paths = []
        for key in ("path", "file_path", "filepath", "file", "source", "destination", "target"):
            value = arguments.get(key)
            if value and isinstance(value, str):
                paths.append(value)
        return paths
