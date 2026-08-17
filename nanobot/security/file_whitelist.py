"""File-level access control using whitelist and blacklist."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from loguru import logger


class FileWhitelist:
    """Control which files the agent can access.

    Uses a whitelist approach: only explicitly allowed files can be accessed.
    """

    def __init__(self, allowed_paths: Iterable[str] | None = None):
        self.allowed: set[Path] = set()
        if allowed_paths:
            for path_str in allowed_paths:
                try:
                    resolved = Path(path_str).resolve()
                    self.allowed.add(resolved)
                except Exception as e:
                    logger.warning(f"Failed to resolve whitelist path: {path_str}: {e}")

    def is_allowed(self, file_path: str | Path) -> bool:
        """Check if a file is in the whitelist.

        Args:
            file_path: Path to check

        Returns:
            True if file is allowed, False otherwise
        """
        if not self.allowed:
            # Empty whitelist means allow all (disabled mode)
            return True

        try:
            resolved = Path(file_path).resolve()
            return resolved in self.allowed
        except Exception as e:
            logger.warning(f"Failed to resolve path for whitelist check: {file_path}: {e}")
            return False

    def add(self, file_path: str | Path) -> None:
        """Add a file to the whitelist.

        Args:
            file_path: Path to add
        """
        try:
            resolved = Path(file_path).resolve()
            self.allowed.add(resolved)
            logger.debug(f"Added to whitelist: {resolved}")
        except Exception as e:
            logger.warning(f"Failed to add to whitelist: {file_path}: {e}")

    def remove(self, file_path: str | Path) -> None:
        """Remove a file from the whitelist.

        Args:
            file_path: Path to remove
        """
        try:
            resolved = Path(file_path).resolve()
            self.allowed.discard(resolved)
            logger.debug(f"Removed from whitelist: {resolved}")
        except Exception as e:
            logger.warning(f"Failed to remove from whitelist: {file_path}: {e}")

    def clear(self) -> None:
        """Clear all whitelist entries."""
        self.allowed.clear()
        logger.debug("Whitelist cleared")

    def to_list(self) -> list[str]:
        """Export whitelist as a list of path strings.

        Returns:
            List of absolute path strings
        """
        return [str(p) for p in sorted(self.allowed)]


class FileBlacklist:
    """Block access to specific files.

    Uses a blacklist approach: explicitly blocked files cannot be accessed.
    """

    def __init__(self, blocked_paths: Iterable[str] | None = None):
        self.blocked: set[Path] = set()
        if blocked_paths:
            for path_str in blocked_paths:
                try:
                    resolved = Path(path_str).resolve()
                    self.blocked.add(resolved)
                except Exception as e:
                    logger.warning(f"Failed to resolve blacklist path: {path_str}: {e}")

    def is_blocked(self, file_path: str | Path) -> bool:
        """Check if a file is in the blacklist.

        Args:
            file_path: Path to check

        Returns:
            True if file is blocked, False otherwise
        """
        if not self.blocked:
            return False

        try:
            resolved = Path(file_path).resolve()
            return resolved in self.blocked
        except Exception as e:
            logger.warning(f"Failed to resolve path for blacklist check: {file_path}: {e}")
            return False

    def add(self, file_path: str | Path) -> None:
        """Add a file to the blacklist.

        Args:
            file_path: Path to add
        """
        try:
            resolved = Path(file_path).resolve()
            self.blocked.add(resolved)
            logger.debug(f"Added to blacklist: {resolved}")
        except Exception as e:
            logger.warning(f"Failed to add to blacklist: {file_path}: {e}")

    def remove(self, file_path: str | Path) -> None:
        """Remove a file from the blacklist.

        Args:
            file_path: Path to remove
        """
        try:
            resolved = Path(file_path).resolve()
            self.blocked.discard(resolved)
            logger.debug(f"Removed from blacklist: {resolved}")
        except Exception as e:
            logger.warning(f"Failed to remove from blacklist: {file_path}: {e}")

    def clear(self) -> None:
        """Clear all blacklist entries."""
        self.blocked.clear()
        logger.debug("Blacklist cleared")

    def to_list(self) -> list[str]:
        """Export blacklist as a list of path strings.

        Returns:
            List of absolute path strings
        """
        return [str(p) for p in sorted(self.blocked)]


class FileAccessControl:
    """Combined whitelist and blacklist access control.

    Checks both whitelist (if enabled) and blacklist to determine access.
    """

    def __init__(
        self,
        whitelist: FileWhitelist | None = None,
        blacklist: FileBlacklist | None = None,
    ):
        self.whitelist = whitelist or FileWhitelist()
        self.blacklist = blacklist or FileBlacklist()

    def check_access(self, file_path: str | Path) -> tuple[bool, str | None]:
        """Check if a file can be accessed.

        Args:
            file_path: Path to check

        Returns:
            Tuple of (allowed, reason). If allowed is False, reason explains why.
        """
        # Check blacklist first (takes precedence)
        if self.blacklist.is_blocked(file_path):
            return False, f"File is blacklisted: {file_path}"

        # Check whitelist
        if not self.whitelist.is_allowed(file_path):
            return False, f"File not in whitelist: {file_path}"

        return True, None

    def require_access(self, file_path: str | Path) -> None:
        """Check access and raise PermissionError if denied.

        Args:
            file_path: Path to check

        Raises:
            PermissionError: If access is denied
        """
        allowed, reason = self.check_access(file_path)
        if not allowed:
            raise PermissionError(reason or "Access denied")
