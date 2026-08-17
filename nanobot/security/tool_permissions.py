"""Tool-level permission control for sensitive data access."""

from __future__ import annotations

from typing import Any

from loguru import logger


class ToolPermissionManager:
    """Control tool access to sensitive resources.

    Manages permissions at the tool-resource-action level to prevent
    unauthorized access to sensitive data files.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.permissions: dict[str, dict[str, dict[str, Any]]] = {}
        if config:
            self.permissions = config.get("tool_permissions", {})

    def check(self, tool_name: str, resource: str, action: str) -> bool:
        """Check if a tool has permission to perform an action on a resource.

        Args:
            tool_name: Name of the tool (e.g., "excel_analyzer")
            resource: Resource identifier (e.g., file path)
            action: Action type ("read", "write", "execute")

        Returns:
            True if permission is granted, False otherwise
        """
        tool_perms = self.permissions.get(tool_name, {})
        resource_perms = tool_perms.get(resource, {})
        allowed_actions = resource_perms.get("allowed_actions", [])
        return action in allowed_actions

    def check_operation(
        self, tool_name: str, resource: str, operation: str
    ) -> bool:
        """Check if a specific operation is allowed on a resource.

        Args:
            tool_name: Name of the tool
            resource: Resource identifier
            operation: Operation name (e.g., "filter", "aggregate")

        Returns:
            True if operation is allowed, False otherwise
        """
        tool_perms = self.permissions.get(tool_name, {})
        resource_perms = tool_perms.get(resource, {})

        # Check forbidden operations first
        forbidden = resource_perms.get("forbidden_operations", [])
        if operation in forbidden:
            return False

        # Check allowed operations
        allowed = resource_perms.get("allowed_operations", [])
        if allowed:  # If whitelist exists, operation must be in it
            return operation in allowed

        # If no whitelist, allow by default (unless forbidden)
        return True

    def get_note(self, tool_name: str, resource: str) -> str | None:
        """Get access note/restriction message for a resource.

        Args:
            tool_name: Name of the tool
            resource: Resource identifier

        Returns:
            Note string if present, None otherwise
        """
        tool_perms = self.permissions.get(tool_name, {})
        resource_perms = tool_perms.get(resource, {})
        return resource_perms.get("note")

    def add_permission(
        self,
        tool_name: str,
        resource: str,
        allowed_actions: list[str] | None = None,
        allowed_operations: list[str] | None = None,
        forbidden_operations: list[str] | None = None,
        note: str | None = None,
    ) -> None:
        """Add or update permissions for a tool-resource pair.

        Args:
            tool_name: Name of the tool
            resource: Resource identifier
            allowed_actions: List of allowed actions
            allowed_operations: List of allowed operations
            forbidden_operations: List of forbidden operations
            note: Optional note about access restrictions
        """
        if tool_name not in self.permissions:
            self.permissions[tool_name] = {}

        resource_perms: dict[str, Any] = {}
        if allowed_actions is not None:
            resource_perms["allowed_actions"] = allowed_actions
        if allowed_operations is not None:
            resource_perms["allowed_operations"] = allowed_operations
        if forbidden_operations is not None:
            resource_perms["forbidden_operations"] = forbidden_operations
        if note is not None:
            resource_perms["note"] = note

        self.permissions[tool_name][resource] = resource_perms
        logger.debug(
            f"Updated permissions: {tool_name} -> {resource}: {resource_perms}"
        )

    def remove_permission(self, tool_name: str, resource: str) -> None:
        """Remove all permissions for a tool-resource pair.

        Args:
            tool_name: Name of the tool
            resource: Resource identifier
        """
        if tool_name in self.permissions:
            self.permissions[tool_name].pop(resource, None)
            logger.debug(f"Removed permissions: {tool_name} -> {resource}")

    def to_dict(self) -> dict[str, Any]:
        """Export permissions configuration as a dictionary.

        Returns:
            Dictionary suitable for JSON serialization
        """
        return {"tool_permissions": self.permissions}

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> ToolPermissionManager:
        """Create a ToolPermissionManager from a configuration dictionary.

        Args:
            config: Configuration dictionary with "tool_permissions" key

        Returns:
            Configured ToolPermissionManager instance
        """
        return cls(config)
