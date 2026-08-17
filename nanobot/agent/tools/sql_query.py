"""SQL query tool for Excel data import and SQL-based analysis."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
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


class SQLQueryConfig(Base):
    """SQL query tool configuration."""

    enable: bool = True
    allowed_paths: list[str] = []  # Allowed Excel file paths for import
    max_rows: int = 100000  # Maximum rows to import
    query_timeout: int = 30  # Query timeout in seconds


@tool_parameters(
    tool_parameters_schema(
        operation=StringSchema(
            "Operation to perform: import_excel, query, list_tables, describe_table"
        ),
        file_path=StringSchema(
            "Path to the Excel file (for import_excel operation)",
            nullable=True,
        ),
        table_name=StringSchema(
            "Table name to import into or query from",
            nullable=True,
        ),
        sheet_name=StringSchema(
            "Sheet name to import (default: first sheet)",
            nullable=True,
        ),
        sql=StringSchema(
            "SQL query to execute (for query operation)",
            nullable=True,
        ),
        limit=IntegerSchema(
            description="Maximum number of rows to return (default: 1000)",
            minimum=1,
            maximum=10000,
            nullable=True,
        ),
    )
)
class SQLQueryTool(Tool):
    """Tool for importing Excel data into SQLite and executing SQL queries."""

    config_key = "sql_query"

    @classmethod
    def config_cls(cls):
        return SQLQueryConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.sql_query.enable

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        cfg = ctx.config.sql_query
        return cls(
            allowed_paths=cfg.allowed_paths,
            max_rows=cfg.max_rows,
            query_timeout=cfg.query_timeout,
        )

    def __init__(
        self,
        allowed_paths: list[str] | None = None,
        max_rows: int = 100000,
        query_timeout: int = 30,
    ):
        self._allowed_paths = [Path(p).resolve() for p in (allowed_paths or [])]
        self._max_rows = max_rows
        self._query_timeout = query_timeout
        # In-memory SQLite database per tool instance
        self._db_path = ":memory:"
        self._conn: sqlite3.Connection | None = None

    @property
    def name(self) -> str:
        return "sql_query"

    @property
    def description(self) -> str:
        return (
            "Import Excel data into SQLite and execute SQL queries for complex analysis. "
            "Supports JOIN, GROUP BY, window functions, and subqueries. "
            "Use for complex financial data queries that are hard to express with pandas."
        )

    @property
    def read_only(self) -> bool:
        return False  # Modifies in-memory database

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _check_path_allowed(self, file_path: str) -> str | None:
        """Check if file path is allowed. Returns error message if not allowed."""
        path = Path(file_path).resolve()
        if not path.exists():
            return f"File not found: {file_path}"
        if not path.suffix.lower() in (".xlsx", ".xls", ".csv"):
            return f"Unsupported file type: {path.suffix}. Only .xlsx, .xls, .csv are supported."
        if self._allowed_paths:
            if not any(path == allowed or allowed in path.parents for allowed in self._allowed_paths):
                return f"Path not allowed: {file_path}. Allowed paths: {[str(p) for p in self._allowed_paths]}"
        return None

    def _format_result(self, data: Any, limit: int = 1000) -> str:
        """Format result for LLM consumption."""
        if isinstance(data, pd.DataFrame):
            if len(data) > limit:
                preview = data.head(limit)
                note = f"\n\n(Showing first {limit} of {len(data)} rows)"
            else:
                preview = data
                note = ""
            
            result_dict = {
                "rows": len(data),
                "columns": list(data.columns),
                "data": preview.to_dict(orient="records"),
            }
            return json.dumps(result_dict, ensure_ascii=False, indent=2, default=str) + note
        
        elif isinstance(data, (dict, list)):
            return json.dumps(data, ensure_ascii=False, indent=2, default=str)
        
        else:
            return str(data)

    async def execute(self, **kwargs: Any) -> Any:
        """Execute SQL operation."""
        operation = kwargs.get("operation")
        
        if not operation:
            return ToolResult.error("Error: operation is required")

        try:
            conn = self._get_connection()

            if operation == "import_excel":
                file_path = kwargs.get("file_path")
                table_name = kwargs.get("table_name")
                sheet_name = kwargs.get("sheet_name")
                
                if not file_path:
                    return ToolResult.error("Error: file_path is required for import_excel operation")
                if not table_name:
                    return ToolResult.error("Error: table_name is required for import_excel operation")

                # Check path permissions
                error = self._check_path_allowed(file_path)
                if error:
                    return ToolResult.error(f"Error: {error}")

                # Load Excel file
                path = Path(file_path)
                if path.suffix.lower() == ".csv":
                    df = pd.read_csv(path)
                else:
                    df = pd.read_excel(path, sheet_name=sheet_name or 0)
                
                if len(df) > self._max_rows:
                    logger.warning(
                        f"Excel file has {len(df)} rows, exceeding max_rows={self._max_rows}. "
                        f"Truncating to first {self._max_rows} rows."
                    )
                    df = df.head(self._max_rows)

                # Import to SQLite
                df.to_sql(table_name, conn, if_exists="replace", index=False)
                
                result = {
                    "table_name": table_name,
                    "rows_imported": len(df),
                    "columns": list(df.columns),
                    "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                }
                return self._format_result(result)

            elif operation == "query":
                sql = kwargs.get("sql")
                limit = kwargs.get("limit", 1000)
                
                if not sql:
                    return ToolResult.error("Error: sql is required for query operation")

                # Execute query
                df = pd.read_sql_query(sql, conn)
                
                result = {
                    "sql": sql,
                    "rows": len(df),
                    "columns": list(df.columns),
                    "data": df,
                }
                return self._format_result(result, limit)

            elif operation == "list_tables":
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
                tables = [row[0] for row in cursor.fetchall()]
                
                result = {
                    "tables": tables,
                    "count": len(tables),
                }
                return self._format_result(result)

            elif operation == "describe_table":
                table_name = kwargs.get("table_name")
                
                if not table_name:
                    return ToolResult.error("Error: table_name is required for describe_table operation")

                cursor = conn.execute(f"PRAGMA table_info({table_name})")
                columns = [
                    {
                        "cid": row[0],
                        "name": row[1],
                        "type": row[2],
                        "notnull": bool(row[3]),
                        "default": row[4],
                        "pk": bool(row[5]),
                    }
                    for row in cursor.fetchall()
                ]
                
                # Get row count
                count_cursor = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
                row_count = count_cursor.fetchone()[0]
                
                result = {
                    "table_name": table_name,
                    "row_count": row_count,
                    "columns": columns,
                }
                return self._format_result(result)

            else:
                return ToolResult.error(
                    f"Error: Unknown operation '{operation}'. "
                    f"Supported operations: import_excel, query, list_tables, describe_table"
                )

        except Exception as e:
            logger.exception(f"SQL query error: {e}")
            return ToolResult.error(f"Error during SQL operation: {str(e)}")

    def __del__(self):
        """Clean up database connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
