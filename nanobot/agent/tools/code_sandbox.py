"""Python code execution sandbox for data analysis.

Provides a secure, restricted environment for executing Python code
with pandas, numpy, and other data analysis libraries. Designed for
ad-hoc computations that SQL and predefined tools cannot express.
"""

from __future__ import annotations

import io
import json
import signal
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.config_base import Base


class CodeSandboxConfig(Base):
    """Code sandbox tool configuration."""

    enable: bool = True
    allowed_paths: list[str] = []  # Allowed file paths for data loading
    timeout: int = 30  # Execution timeout in seconds
    max_output_chars: int = 50000  # Maximum output length
    max_code_chars: int = 20000  # Maximum code length
    allow_file_write: bool = False  # Allow writing files (default: read-only)


# ── Safe module whitelist ──────────────────────────────────────────────
# Only these modules can be imported inside the sandbox.
_SAFE_MODULES: dict[str, str] = {
    "pandas": "pd",
    "numpy": "np",
    "json": "json",
    "math": "math",
    "statistics": "statistics",
    "datetime": "datetime",
    "collections": "collections",
    "itertools": "itertools",
    "functools": "functools",
    "re": "re",
    "string": "string",
    "decimal": "decimal",
    "fractions": "fractions",
    "copy": "copy",
    "textwrap": "textwrap",
}

# ── Blocked names ──────────────────────────────────────────────────────
# These names are removed from the sandbox globals to prevent escape.
_BLOCKED_NAMES: set[str] = {
    "__import__",
    "open",
    "exec",
    "eval",
    "compile",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
    "hasattr",
    "breakpoint",
    "exit",
    "quit",
    "input",
    "__build_class__",
    "__loader__",
    "__spec__",
}


def _build_safe_globals(
    stdout: io.StringIO,
    stderr: io.StringIO,
    allowed_paths: list[Path],
    allow_file_write: bool,
) -> dict[str, Any]:
    """Build a restricted globals dict for sandboxed execution."""
    safe_builtins: dict[str, Any] = {}

    # Copy safe builtins (functions only, no dangerous ones)
    import builtins

    for name in dir(builtins):
        if name.startswith("_"):
            continue
        if name in _BLOCKED_NAMES:
            continue
        safe_builtins[name] = getattr(builtins, name)

    # Provide a restricted __import__ that only allows whitelisted modules
    _original_import = builtins.__import__
    
    def _sandbox_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        """Restricted import: only allow whitelisted modules."""
        # Extract top-level module name
        top_level = name.split('.')[0]
        
        if top_level not in _SAFE_MODULES:
            raise ImportError(
                f"Module '{name}' is not allowed in sandbox. "
                f"Allowed modules: {', '.join(sorted(_SAFE_MODULES.keys()))}"
            )
        
        return _original_import(name, globals, locals, fromlist, level)
    
    safe_builtins["__import__"] = _sandbox_import

    # Provide a restricted open() that only reads allowed paths
    def _sandbox_open(
        file: str | Path,
        mode: str = "r",
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Restricted open: only read mode on allowed paths."""
        path = Path(file).resolve()

        # Check write permission
        if any(m in mode for m in ("w", "a", "x", "+")) and not allow_file_write:
            raise PermissionError(
                f"Write mode '{mode}' not allowed. Sandbox is read-only for files."
            )

        # Check path is in allowed list
        if allowed_paths:
            path_ok = any(
                path == allowed or allowed in path.parents or path.is_relative_to(allowed)
                for allowed in allowed_paths
            )
            if not path_ok:
                raise PermissionError(
                    f"Path not allowed: {path}\n"
                    f"Allowed paths: {[str(p) for p in allowed_paths]}"
                )

        return builtins.open(str(path), mode, *args, **kwargs)

    safe_builtins["open"] = _sandbox_open

    # Build globals
    safe_globals: dict[str, Any] = {"__builtins__": safe_builtins}

    # Import whitelisted modules
    for module_name, alias in _SAFE_MODULES.items():
        try:
            mod = __import__(module_name)
            safe_globals[alias] = mod
            # Also make it available by its real name
            if alias != module_name:
                safe_globals[module_name] = mod
        except ImportError:
            logger.debug(f"Optional module not available: {module_name}")

    # Provide a restricted print that writes to our captured stdout
    _original_print = safe_builtins.get("print")

    def _sandbox_print(*args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("file", stdout)
        _original_print(*args, **kwargs)

    safe_builtins["print"] = _sandbox_print

    return safe_globals


def _truncate_output(text: str, max_chars: int) -> str:
    """Truncate output if too long, keeping head and tail."""
    if len(text) <= max_chars:
        return text

    keep = max_chars // 2
    truncated = (
        text[:keep]
        + f"\n\n... [output truncated: {len(text)} chars total, showing first {keep} and last {keep}] ...\n\n"
        + text[-keep:]
    )
    return truncated


@tool_parameters(
    tool_parameters_schema(
        code=StringSchema(
            "Python code to execute in the sandbox. "
            "Available modules: pandas (pd), numpy (np), json, math, statistics, "
            "datetime, collections, itertools, functools, re, string, decimal. "
            "Use print() to output results. "
            "Use pd.read_excel() or pd.read_csv() to load data files."
        ),
        code_description=StringSchema(
            "Brief description of what this code does (for logging)",
            nullable=True,
        ),
        timeout=IntegerSchema(
            description="Execution timeout in seconds (default: 30, max: 120)",
            minimum=1,
            maximum=120,
            nullable=True,
        ),
    )
)
class CodeSandboxTool(Tool):
    """Execute Python code in a restricted sandbox for data analysis.

    Provides pandas, numpy, and standard library access for ad-hoc
    computations that SQL queries cannot express. File reading is
    restricted to allowed paths only.
    """

    config_key = "code_sandbox"

    @classmethod
    def config_cls(cls):
        return CodeSandboxConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.code_sandbox.enable

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        cfg = ctx.config.code_sandbox
        return cls(
            allowed_paths=cfg.allowed_paths,
            timeout=cfg.timeout,
            max_output_chars=cfg.max_output_chars,
            max_code_chars=cfg.max_code_chars,
            allow_file_write=cfg.allow_file_write,
        )

    def __init__(
        self,
        allowed_paths: list[str] | None = None,
        timeout: int = 30,
        max_output_chars: int = 50000,
        max_code_chars: int = 20000,
        allow_file_write: bool = False,
    ):
        self._allowed_paths = [Path(p).resolve() for p in (allowed_paths or [])]
        self._timeout = timeout
        self._max_output_chars = max_output_chars
        self._max_code_chars = max_code_chars
        self._allow_file_write = allow_file_write

    @property
    def name(self) -> str:
        return "code_sandbox"

    @property
    def description(self) -> str:
        return (
            "Execute Python code in a secure sandbox for data analysis. "
            "Available: pandas (pd), numpy (np), math, json, statistics, datetime, re. "
            "Use print() to output results. Use pd.read_excel()/pd.read_csv() to load data. "
            "Ideal for: custom calculations, multi-step computations, statistical analysis, "
            "data transformations, and anything SQL cannot express."
        )

    @property
    def read_only(self) -> bool:
        return not self._allow_file_write

    def _check_path_allowed(self, file_path: str) -> str | None:
        """Check if a file path is allowed. Returns error message if not."""
        path = Path(file_path).resolve()
        if not path.exists():
            return f"File not found: {file_path}"
        if self._allowed_paths:
            if not any(
                path == allowed or allowed in path.parents
                for allowed in self._allowed_paths
            ):
                return (
                    f"Path not allowed: {file_path}. "
                    f"Allowed: {[str(p) for p in self._allowed_paths]}"
                )
        return None

    async def execute(self, **kwargs: Any) -> Any:
        """Execute Python code in the sandbox."""
        code = kwargs.get("code")
        code_description = kwargs.get("code_description", "")
        timeout = kwargs.get("timeout", self._timeout)

        if not code:
            return ToolResult.error("Error: 'code' parameter is required")

        if not isinstance(code, str):
            return ToolResult.error("Error: 'code' must be a string")

        # Enforce code length limit
        if len(code) > self._max_code_chars:
            return ToolResult.error(
                f"Error: Code too long ({len(code)} chars, max {self._max_code_chars})"
            )

        # Clamp timeout
        timeout = min(max(1, timeout), 120)

        logger.info(
            f"[CodeSandbox] Executing code ({len(code)} chars, timeout={timeout}s)"
            + (f": {code_description}" if code_description else "")
        )

        # Capture stdout/stderr
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        # Build safe execution environment
        safe_globals = _build_safe_globals(
            stdout_capture,
            stderr_capture,
            self._allowed_paths,
            self._allow_file_write,
        )

        # Execute with timeout
        result_value = None
        error_message = None
        execution_time = 0.0

        import time

        start_time = time.time()

        try:
            # Use threading-based timeout for cross-platform compatibility
            import concurrent.futures

            def _run_code() -> Any:
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    # Compile first to catch syntax errors
                    compiled = compile(code, "<sandbox>", "exec")
                    # Execute in restricted globals
                    local_ns: dict[str, Any] = {}
                    exec(compiled, safe_globals, local_ns)  # noqa: S102
                    return local_ns.get("_result", None)

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_code)
                result_value = future.result(timeout=timeout)

        except concurrent.futures.TimeoutError:
            error_message = (
                f"Execution timed out after {timeout} seconds. "
                "Try simplifying the code or reducing data size."
            )
        except SyntaxError as e:
            error_message = f"Syntax error at line {e.lineno}: {e.msg}"
        except PermissionError as e:
            error_message = f"Permission denied: {e}"
        except Exception as e:
            # Capture the traceback for debugging
            tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
            # Filter out internal frames
            user_tb = [
                line
                for line in tb_lines
                if "<sandbox>" in line or not line.strip().startswith("File")
            ]
            error_message = f"{type(e).__name__}: {e}"
            if user_tb:
                error_message += "\n" + "".join(user_tb[-3:])  # Last 3 frames

        execution_time = time.time() - start_time

        # Collect output
        stdout_text = stdout_capture.getvalue()
        stderr_text = stderr_capture.getvalue()

        # Build result
        output_parts: list[str] = []

        if stdout_text.strip():
            output_parts.append(f"## Output\n```\n{_truncate_output(stdout_text.strip(), self._max_output_chars)}\n```")

        if stderr_text.strip():
            output_parts.append(f"## Warnings\n```\n{_truncate_output(stderr_text.strip(), 5000)}\n```")

        if error_message:
            output_parts.append(f"## Error\n```\n{error_message}\n```")

        if result_value is not None and not stdout_text.strip():
            # If code set _result but didn't print, show it
            try:
                result_str = json.dumps(result_value, ensure_ascii=False, default=str, indent=2)
                output_parts.append(f"## Result\n```json\n{result_str}\n```")
            except (TypeError, ValueError):
                output_parts.append(f"## Result\n```\n{result_value}\n```")

        # Metadata
        output_parts.append(
            f"\n---\n*Execution time: {execution_time:.2f}s | "
            f"Code: {len(code)} chars*"
        )

        final_output = "\n\n".join(output_parts)

        if error_message:
            return ToolResult.error(final_output)
        return final_output
