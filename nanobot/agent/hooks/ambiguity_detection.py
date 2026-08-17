"""Ambiguity detection and interruption hook for agent runs.

Detects uncertainty in LLM reasoning, ambiguous tool parameters,
anomalous tool results, and low-confidence answers. Pauses execution
and asks the user for clarification when ambiguity is detected.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext
from nanobot.providers.base import ToolCallRequest


class AmbiguityPauseException(Exception):
    """Raised to pause the runner loop and wait for user clarification."""

    def __init__(self, question: str, context: dict[str, Any] | None = None):
        self.question = question
        self.pause_context = context or {}
        super().__init__(question)


class AmbiguityDecisionCache:
    """Cache user decisions on ambiguity questions to avoid repeated prompts."""

    def __init__(self, max_size: int = 50):
        self._cache: dict[str, str] = {}
        self._max_size = max_size

    def get_decision(self, ambiguity_desc: str) -> str | None:
        """Get cached user decision for a similar ambiguity."""
        key = self._hash_ambiguity(ambiguity_desc)
        return self._cache.get(key)

    def save_decision(self, ambiguity_desc: str, decision: str) -> None:
        """Save user decision for future reference."""
        if len(self._cache) >= self._max_size:
            # Evict oldest entry
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
        key = self._hash_ambiguity(ambiguity_desc)
        self._cache[key] = decision

    @staticmethod
    def _hash_ambiguity(desc: str) -> str:
        """Normalize ambiguity description and hash it."""
        normalized = re.sub(r"'[^']*'", "'X'", desc)
        normalized = re.sub(r'"[^"]*"', '"X"', normalized)
        normalized = re.sub(r"\d+", "N", normalized)
        return hashlib.md5(normalized.encode()).hexdigest()


class AmbiguityInterruptionHook(AgentHook):
    """Detect ambiguity during agent execution and pause for user clarification.

    Monitors four detection points:
    1. LLM reasoning uncertainty (after streaming)
    2. Tool parameter ambiguity (before tool execution)
    3. Tool result anomaly (after tool execution)
    4. Final answer confidence (in finalize_content)
    """

    # Business term → expected column mapping for concept confusion detection
    CONCEPT_COLUMN_MAP = {
        "逾期金额": ["订单金额", "D30累计支付金额"],  # 逾期 = 订单金额 - D30累计支付金额
        "贷款余额": ["订单金额"],
        "案件量": ["客户数"],
        "回款率": ["累计支付金额", "订单金额"],
        "回款金额": ["累计支付金额"],
    }

    # Uncertainty expression patterns (Chinese + English)
    UNCERTAINTY_PATTERNS = [
        # English
        r"I'm (not sure|uncertain|unsure)",
        r"It('s| is) (unclear|ambiguous|not clear)",
        r"I (don't|do not) know (which|what|how)",
        r"There (are|could be) multiple (interpretations|ways|options)",
        r"Should I (assume|use|choose)",
        r"Which (one|option|method) should",
        # Chinese
        r"不确定|不清楚|不明确|模棱两可",
        r"可能有多种(理解|解释|方式)",
        r"应该(假设|使用|选择)哪个",
        r"是.*还是.*[？?]",
    ]

    # Low confidence patterns for final answers
    LOW_CONFIDENCE_PATTERNS = [
        r"(大约|大概|可能|也许|或许)",
        r"(approximately|about|maybe|perhaps)",
        r"(估计|推测|猜测)",
        r"(estimate|guess|speculate)",
    ]

    def __init__(
        self,
        *,
        detect_reasoning_uncertainty: bool = True,
        detect_tool_parameter_ambiguity: bool = True,
        detect_tool_result_anomaly: bool = True,
        detect_low_confidence_answer: bool = True,
        max_pause_per_turn: int = 3,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        reraise: bool = False,
    ):
        super().__init__(reraise=reraise)
        self._detect_reasoning = detect_reasoning_uncertainty
        self._detect_params = detect_tool_parameter_ambiguity
        self._detect_result = detect_tool_result_anomaly
        self._detect_confidence = detect_low_confidence_answer
        self._max_pause = max_pause_per_turn
        self._on_progress = on_progress
        self._pause_count = 0
        self._decision_cache = AmbiguityDecisionCache()
        self._reasoning_buffer = ""
        self._pending_confidence_warning: str | None = None  # set by finalize_content, raised in after_iteration

        self._uncertainty_regex = re.compile(
            "|".join(self.UNCERTAINTY_PATTERNS), re.IGNORECASE
        )
        self._confidence_regex = re.compile(
            "|".join(self.LOW_CONFIDENCE_PATTERNS), re.IGNORECASE
        )

    # ── Streaming: accumulate reasoning text ──

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        """Accumulate streamed content for reasoning analysis."""
        if context.streamed_reasoning:
            self._reasoning_buffer += delta

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        """Check accumulated reasoning for uncertainty after stream ends."""
        if not self._detect_reasoning or not self._reasoning_buffer:
            self._reasoning_buffer = ""
            return

        reasoning = self._reasoning_buffer
        self._reasoning_buffer = ""

        if self._pause_count >= self._max_pause:
            return

        ambiguity = self._extract_ambiguity(reasoning)
        if ambiguity:
            # Check decision cache first
            cached = self._decision_cache.get_decision(ambiguity)
            if cached:
                logger.info(f"Using cached ambiguity decision: {ambiguity} -> {cached}")
                return

            self._pause_count += 1
            question = (
                f"检测到推理歧义：{ambiguity}\n"
                "请澄清您的意图，或输入 /skip 跳过此问题。"
            )
            raise AmbiguityPauseException(question, {
                "type": "reasoning_uncertainty",
                "ambiguity": ambiguity,
                "iteration": context.iteration,
            })

    # ── Tool parameter ambiguity ──

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
    ) -> None:
        """Check tool parameters for ambiguity before execution."""
        if not self._detect_params or self._pause_count >= self._max_pause:
            return

        if not isinstance(params, dict):
            return

        tool_name = getattr(tool, "name", str(tool_call.name))
        ambiguity = self._detect_parameter_ambiguity(tool_name, params)
        if ambiguity:
            self._pause_count += 1
            question = (
                f"检测到工具参数歧义：{ambiguity}\n"
                "请确认参数是否正确，或输入 /skip 跳过。"
            )
            raise AmbiguityPauseException(question, {
                "type": "tool_parameter_ambiguity",
                "tool": tool_name,
                "ambiguity": ambiguity,
                "iteration": context.iteration,
            })

    # ── Tool result anomaly ──

    async def after_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        result: Any,
    ) -> None:
        """Check tool results for anomalies after execution."""
        if not self._detect_result or self._pause_count >= self._max_pause:
            return

        tool_name = getattr(tool, "name", str(tool_call.name))
        anomaly = self._detect_result_anomaly(tool_name, result)
        if anomaly:
            self._pause_count += 1
            question = (
                f"检测到结果异常：{anomaly}\n"
                "请确认是否继续，或提供修正方案。输入 /skip 跳过。"
            )
            raise AmbiguityPauseException(question, {
                "type": "tool_result_anomaly",
                "tool": tool_name,
                "anomaly": anomaly,
                "iteration": context.iteration,
            })

    # ── Final answer confidence ──

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        """Check final answer for low confidence expressions.

        Since finalize_content is synchronous and cannot raise exceptions,
        we store the warning and raise AmbiguityPauseException in the next
        after_iteration call, which IS async and CAN pause the runner loop
        to ask the user for clarification.
        """
        if not self._detect_confidence or not content:
            return content
        if self._pause_count >= self._max_pause:
            return content

        if self._confidence_regex.search(content):
            logger.warning(
                "Low confidence detected in final answer: {}",
                content[:100],
            )
            # Store for after_iteration to raise as AmbiguityPauseException
            self._pending_confidence_warning = content[:200]
            # Also inject a visible warning into the content
            warning = (
                "\n\n⚠️ **注意**：答案中包含不确定性表述（大约/可能/估计等），"
                "建议重新检查计算逻辑。\n"
            )
            return content + warning
        return content

    async def after_iteration(self, context: AgentHookContext) -> None:
        """Raise pending low-confidence pause after iteration completes.

        finalize_content cannot raise (it returns str), so we defer the
        AmbiguityPauseException to this async hook point. The runner's
        try/except will catch it and call _handle_ambiguity_pause to
        ask the user for clarification.
        """
        if self._pending_confidence_warning is not None:
            warning_text = self._pending_confidence_warning
            self._pending_confidence_warning = None
            self._pause_count += 1
            question = (
                f"检测到答案包含不确定性表述（大约/可能/估计等），"
                f"答案可能不够准确。\n"
                f"答案摘要：{warning_text}\n\n"
                f"建议：\n"
                f"1. 重新检查 SQL 查询逻辑\n"
                f"2. 重新检查 SQL 查询逻辑\n"
                f"3. 输入 /skip 跳过此警告继续使用当前答案"
            )
            raise AmbiguityPauseException(question, {
                "type": "low_confidence_answer",
                "answer_preview": warning_text,
                "iteration": context.iteration,
            })

    # ── Reset per-turn state ──

    async def before_run(self, context: AgentRunHookContext) -> None:
        """Reset pause counter at the start of each run."""
        self._pause_count = 0
        self._reasoning_buffer = ""
        self._pending_confidence_warning = None

    # ── Detection helpers ──

    def _extract_ambiguity(self, text: str) -> str | None:
        """Extract the sentence containing uncertainty expression."""
        sentences = re.split(r"[.!?。！？\n]", text)
        for sentence in sentences:
            sentence = sentence.strip()
            if sentence and self._uncertainty_regex.search(sentence):
                # Truncate long sentences
                if len(sentence) > 200:
                    sentence = sentence[:200] + "..."
                return sentence
        return None

    def _detect_parameter_ambiguity(
        self, tool_name: str, params: dict[str, Any]
    ) -> str | None:
        """Detect ambiguous tool parameters."""
        # Excel analyzer: overly broad filter conditions
        if tool_name == "excel_analyzer":
            query = str(params.get("query", ""))
            if query.count(" or ") + query.count(" OR ") > 3:
                return f"筛选条件包含 {query.count(' or ') + query.count(' OR ')} 个 OR，可能过于宽泛"

        # SQL query: detect common mistakes
        if tool_name == "sql_query":
            sql = str(params.get("sql", ""))
            sql_upper = sql.upper()

            # Too many JOINs without WHERE
            join_count = sql_upper.count("JOIN")
            if join_count > 2 and "WHERE" not in sql_upper:
                return f"SQL 包含 {join_count} 个 JOIN 但没有 WHERE 条件，结果可能过大"

            # Detect exact match on 类型 column (misses subtypes like 机器人1-3, 机器人1-4)
            if "类型" in sql and "LIKE" not in sql_upper:
                # Check for exact equality on 类型
                if re.search(r"类型\s*=\s*['\"]机器人['\"]", sql):
                    return (
                        "类型筛选使用了精确匹配 = '机器人'，"
                        "这会遗漏子类型（机器人1-3, 机器人1-4 等）。"
                        "请改用 LIKE '机器人%' 匹配所有机器人子类型。"
                    )

            # Detect concept confusion: SQL uses wrong columns for the business term
            concept_issue = self._detect_concept_confusion(sql)
            if concept_issue:
                return concept_issue

        return None

    def _detect_concept_confusion(self, sql: str) -> str | None:
        """Detect when SQL columns don't match the expected business concept.

        For example, if the question asks about 逾期金额 but the SQL only
        uses SUM(订单金额) without subtracting D30累计支付金额, this is
        a concept confusion error.
        """
        # Check if SQL references 逾期-related patterns incorrectly
        # 逾期金额 should be: SUM(订单金额) - SUM(D30累计支付金额)
        # If SQL only has SUM(订单金额) and the context mentions 逾期, flag it
        if "逾期" in sql and "订单金额" in sql and "累计支付金额" not in sql:
            return (
                "SQL 中提到了逾期但只使用了订单金额列。"
                "逾期金额 = SUM(订单金额) - SUM(D30累计支付金额)，"
                "请确保 SQL 包含减法运算。"
            )
        return None

    def _detect_result_anomaly(self, tool_name: str, result: Any) -> str | None:
        """Detect anomalous tool results."""
        result_str = str(result) if result is not None else ""

        # Check for empty results
        if '"rows": 0' in result_str or '"filtered_rows": 0' in result_str:
            return "查询/筛选返回 0 行数据，可能筛选条件过严或数据不匹配"

        # Check for very large results (close to full table)
        try:
            import json as _json
            data = _json.loads(result_str) if result_str.startswith("{") else None
            if data and isinstance(data, dict):
                rows = data.get("rows", 0)
                if isinstance(rows, int) and rows > 30000:
                    return f"返回 {rows} 行数据，接近全表，可能筛选条件过宽"
        except Exception:
            pass

        return None

    # ── Decision cache access ──

    @property
    def decision_cache(self) -> AmbiguityDecisionCache:
        """Access the ambiguity decision cache."""
        return self._decision_cache
