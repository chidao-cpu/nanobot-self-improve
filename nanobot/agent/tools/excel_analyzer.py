"""Excel data analysis tool for financial data processing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    IntegerSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.config_base import Base


class ExcelAnalyzerConfig(Base):
    """Excel analyzer tool configuration."""

    enable: bool = True
    allowed_paths: list[str] = []  # Allowed Excel file paths
    read_only: bool = True  # Only allow read operations
    max_rows: int = 100000  # Maximum rows to process


@tool_parameters(
    tool_parameters_schema(
        file_path=StringSchema("Path to the Excel file"),
        operation=StringSchema(
            "Operation to perform: read_sheet, filter, aggregate, pivot, compare, trend"
        ),
        sheet_name=StringSchema(
            "Sheet name to read (default: first sheet)",
            nullable=True,
        ),
        query=StringSchema(
            "Pandas query string for filter operation (e.g., '类型 == \"机器人\" and mon == \"2024-10\"')",
            nullable=True,
        ),
        groupby=ArraySchema(
            items=StringSchema("Column name to group by"),
            description="Columns to group by for aggregate/pivot operations",
            nullable=True,
        ),
        agg_funcs=ObjectSchema(
            description="Aggregation functions mapping column names to functions (e.g., {'订单金额': 'sum', '客户数': 'count'})",
            nullable=True,
        ),
        columns=ArraySchema(
            items=StringSchema("Column name"),
            description="Columns to include in pivot table",
            nullable=True,
        ),
        values=StringSchema(
            "Value column for pivot table",
            nullable=True,
        ),
        aggfunc=StringSchema(
            "Aggregation function for pivot table (default: 'sum')",
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
class ExcelAnalyzerTool(Tool):
    """Tool for reading and analyzing Excel files with pandas."""

    config_key = "excel_analyzer"

    @classmethod
    def config_cls(cls):
        return ExcelAnalyzerConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.excel_analyzer.enable

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        cfg = ctx.config.excel_analyzer
        return cls(
            allowed_paths=cfg.allowed_paths,
            read_only=cfg.read_only,
            max_rows=cfg.max_rows,
        )

    def __init__(
        self,
        allowed_paths: list[str] | None = None,
        read_only: bool = True,
        max_rows: int = 100000,
    ):
        self._allowed_paths = [Path(p).resolve() for p in (allowed_paths or [])]
        self._read_only = read_only
        self._max_rows = max_rows

    @property
    def name(self) -> str:
        return "excel_analyzer"

    @property
    def description(self) -> str:
        return (
            "Read and analyze Excel files with pandas operations. "
            "Supports filtering, aggregation, pivot tables, and trend analysis. "
            "Use for financial data analysis tasks."
        )

    @property
    def read_only(self) -> bool:
        return self._read_only

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

    def _load_dataframe(self, file_path: str, sheet_name: str | None = None) -> pd.DataFrame:
        """Load Excel file into DataFrame."""
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
        
        return df

    def _format_result(self, data: Any, limit: int = 1000) -> str:
        """Format result for LLM consumption."""
        if isinstance(data, pd.DataFrame):
            if len(data) > limit:
                preview = data.head(limit)
                note = f"\n\n(Showing first {limit} of {len(data)} rows)"
            else:
                preview = data
                note = ""
            
            # Convert to dict for JSON serialization
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
        """Execute Excel analysis operation."""
        file_path = kwargs.get("file_path")
        operation = kwargs.get("operation")
        
        if not file_path:
            return ToolResult.error("Error: file_path is required")
        if not operation:
            return ToolResult.error("Error: operation is required")

        # Check path permissions
        error = self._check_path_allowed(file_path)
        if error:
            return ToolResult.error(f"Error: {error}")

        try:
            # Load data
            sheet_name = kwargs.get("sheet_name")
            df = self._load_dataframe(file_path, sheet_name)
            limit = kwargs.get("limit", 1000)

            if operation == "read_sheet":
                # Return sheet info and preview
                info = {
                    "shape": df.shape,
                    "columns": list(df.columns),
                    "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                    "preview": df.head(10).to_dict(orient="records"),
                }
                return self._format_result(info)

            elif operation == "filter":
                query = kwargs.get("query")
                if not query:
                    return ToolResult.error("Error: query is required for filter operation")
                
                filtered = df.query(query)
                result = {
                    "query": query,
                    "original_rows": len(df),
                    "filtered_rows": len(filtered),
                    "data": filtered,
                }
                return self._format_result(result, limit)

            elif operation == "aggregate":
                groupby = kwargs.get("groupby", [])
                agg_funcs = kwargs.get("agg_funcs", {})
                
                if not agg_funcs:
                    return ToolResult.error("Error: agg_funcs is required for aggregate operation")
                
                if groupby:
                    result = df.groupby(groupby).agg(agg_funcs)
                else:
                    result = df.agg(agg_funcs)
                
                return self._format_result(result)

            elif operation == "pivot":
                columns = kwargs.get("columns")
                values = kwargs.get("values")
                aggfunc = kwargs.get("aggfunc", "sum")
                index = kwargs.get("groupby", [])
                
                if not values:
                    return ToolResult.error("Error: values is required for pivot operation")
                
                pivot = pd.pivot_table(
                    df,
                    values=values,
                    index=index if index else None,
                    columns=columns,
                    aggfunc=aggfunc,
                )
                return self._format_result(pivot)

            elif operation == "compare":
                # Cross-period or cross-group comparison
                groupby = kwargs.get("groupby", [])
                values = kwargs.get("values")
                
                if not groupby or not values:
                    return ToolResult.error("Error: groupby and values are required for compare operation")
                
                grouped = df.groupby(groupby)[values].agg(["sum", "mean", "count"])
                return self._format_result(grouped)

            elif operation == "trend":
                # Trend analysis (simple moving average)
                groupby = kwargs.get("groupby", [])
                values = kwargs.get("values")
                
                if not groupby or not values:
                    return ToolResult.error("Error: groupby and values are required for trend operation")
                
                grouped = df.groupby(groupby)[values].sum().reset_index()
                grouped = grouped.sort_values(groupby)
                grouped["moving_avg_3"] = grouped[values].rolling(window=3, min_periods=1).mean()
                
                return self._format_result(grouped)

            else:
                return ToolResult.error(
                    f"Error: Unknown operation '{operation}'. "
                    f"Supported operations: read_sheet, filter, aggregate, pivot, compare, trend"
                )

        except Exception as e:
            logger.exception(f"Excel analysis error: {e}")
            return ToolResult.error(f"Error during Excel analysis: {str(e)}")
