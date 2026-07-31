from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from pydantic import ValidationError

from archcode.conversation.manager import ConversationManager
from archcode.conversation.models import ThinkingBlock, ToolResultBlock, ToolUseBlock
from archcode.llm.client import LLMClient
from archcode.llm.events import (
    StreamEnd,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)
from archcode.llm.serializer import build_anthropic_tools, build_openai_tools
from archcode.prompts import build_plan_mode_reminder
from archcode.tools.base import MAX_OUTPUT_CHARS, ToolResult
from archcode.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# AgentEvent 事件类型
# ---------------------------------------------------------------------------


@dataclass
class StreamText:
    text: str


@dataclass
class ThinkingText:
    text: str


@dataclass
class ToolUseEvent:
    tool_name: str
    tool_id: str
    arguments: dict


@dataclass
class ToolResultEvent:
    """工具执行结果事件（发往 UI 显示）。"""

    tool_id: str
    tool_name: str
    output: str
    is_error: bool
    elapsed: float


@dataclass
class TurnComplete:
    turn: int


@dataclass
class ErrorEvent:
    message: str


@dataclass
class LoopComplete:
    total_turns: int
    text: str = ""


@dataclass
class UsageEvent:
    input_tokens: int
    output_tokens: int
    cache_read: int = 0
    cache_creation: int = 0


@dataclass
class RetryEvent:
    reason: str
    wait: float = 0.0


AgentEvent = (
    StreamText
    | ThinkingText
    | ToolUseEvent
    | ToolResultEvent
    | TurnComplete
    | ErrorEvent
    | LoopComplete
    | UsageEvent
    | RetryEvent
)


# ---------------------------------------------------------------------------
# LLM 响应收集器
# ---------------------------------------------------------------------------


@dataclass
class ThinkingBlock_:
    thinking: str
    signature: str


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCallComplete] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock_] = field(default_factory=list)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0


class StreamCollector:
    def __init__(self) -> None:
        self.response = LLMResponse()

    async def consume(
        self, stream: AsyncIterator[StreamEvent]
    ) -> AsyncIterator[AgentEvent]:
        """消费 LLM 流式事件,边折叠状态边向外 yield AgentEvent。

        调用方拿到的是 ``AgentEvent`` 流:
        - 折叠过的状态读 ``self.response``(text / tool_calls / thinking_blocks / stop_reason / token 用量)
        - 即时 UI 事件从 ``async for event in collector.consume(...)`` 直接拿到
        """
        async for event in stream:
            if isinstance(event, TextDelta):
                self.response.text += event.text
                yield StreamText(text=event.text)
            elif isinstance(event, ThinkingDelta):
                yield ThinkingText(text=event.text)
            elif isinstance(event, ThinkingComplete):
                self.response.thinking_blocks.append(
                    ThinkingBlock_(thinking=event.thinking, signature=event.signature)
                )
            elif isinstance(event, (ToolCallStart, ToolCallDelta)):
                pass
            elif isinstance(event, ToolCallComplete):
                self.response.tool_calls.append(event)
                yield ToolUseEvent(
                    tool_name=event.tool_name,
                    tool_id=event.tool_id,
                    arguments=event.arguments,
                )
            elif isinstance(event, StreamEnd):
                self.response.stop_reason = event.stop_reason
                self.response.input_tokens = event.input_tokens
                self.response.output_tokens = event.output_tokens
                self.response.cache_read = event.cache_read
                self.response.cache_creation = event.cache_creation


# ---------------------------------------------------------------------------
# 工具批量执行
# ---------------------------------------------------------------------------


@dataclass
class ToolBatch:
    concurrent: bool
    calls: list[ToolCallComplete]


def partition_tool_calls(
    tool_calls: list[ToolCallComplete],
    registry: ToolRegistry,
) -> list[ToolBatch]:
    """将 tool_calls 按并发安全性和 registry 状态分组。

    同一个 batch 内的 calls 可以并发执行；不同 batch 之间必须串行。
    """
    batches: list[ToolBatch] = []
    for tc in tool_calls:
        tool = registry.get(tc.tool_name)
        safe = (
            tool is not None
            and tool.is_concurrency_safe
            and registry.is_enabled(tc.tool_name)
        )
        if safe and batches and batches[-1].concurrent:
            batches[-1].calls.append(tc)
        else:
            batches.append(ToolBatch(concurrent=safe, calls=[tc]))
    return batches


# ---------------------------------------------------------------------------
# Agent 主循环
# ---------------------------------------------------------------------------


class Agent:
    """Agent 循环：用户消息 → LLM 流式事件 → 工具执行 → 结果写回 → 循环直到模型结束。

    完整 ReAct 循环（v0.3+）：
    - while True 驱动，max_iterations 为硬上限
    - 每轮：stream LLM → 收集 TextDelta + ToolCallComplete →
           无 tool_calls → TurnComplete + LoopComplete 退出
           有 tool_calls → 执行所有工具 → 写 ToolResultBlock →
           回到循环顶部继续
    - consecutive_unknown 连续 3 次未知工具则退出
    - max_tokens 停止原因触发重试逻辑

    Plan Mode（可选）：
    - 通过 ``plan_mode=True`` 开启,_execute_tool 拦截非 read 工具并提示用户先关掉开关
    - plan 文件路径:``<work_dir>/.archcode/plans/{slug}.md``
    - 用 ``set_plan_mode(True/False)`` 切换
    - plan reminder 不入 system 字段,通过每轮 ``conversation.add_system_reminder()`` 注入到 messages
    """

    # plan mode reminder 文本已搬到 prompts/reminders.py,这里只留路径生成

    _ADJECTIVES = ["bold", "bright", "calm", "deep", "fair", "fast",
                   "glad", "keen", "kind", "neat", "pure", "safe",
                   "soft", "warm", "wise", "swift", "vivid"]
    _NOUNS = ["sketch", "draft", "spark", "trail", "ridge", "grove",
              "field", "forge", "frost", "haven", "pearl", "stone",
              "river", "tower", "delta", "orbit", "pulse", "shore"]

    def __init__(
        self,
        client: LLMClient,
        system_prompt: str,
        tool_registry: ToolRegistry | None = None,
        max_output_tokens: int = 4096,
        max_iterations: int = 50,
        work_dir: str | Path | None = None,
        plan_mode: bool = False,
    ) -> None:
        self._client = client
        self._system_prompt = system_prompt
        self._plan_mode = False
        self._plan_path: Path | None = None
        self._tool_registry = tool_registry
        self._max_iterations = max_iterations
        self._work_dir = Path(work_dir).resolve() if work_dir else None
        self._client.set_max_output_tokens(max_output_tokens)
        if plan_mode:
            self.set_plan_mode(True)

    def set_plan_mode(self, on: bool) -> None:
        """切换 plan mode。

        plan reminder 不入 system 字段,而是通过每轮
        ``conversation.add_system_reminder()`` 注入到 messages 数组,
        开启时顺便生成 plan 文件路径。
        """
        self._plan_mode = on
        if on:
            self._plan_path = self._get_plan_path()

    def _get_plan_path(self) -> Path:
        """生成 plan 文件路径 <work_dir>/.archcode/plans/{slug}.md。"""
        import datetime
        import random

        base = self._work_dir or Path.cwd()
        plans_dir = base / ".archcode" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%m%d-%H%M")
        slug = f"{random.choice(self._ADJECTIVES)}-{random.choice(self._NOUNS)}-{ts}"
        return plans_dir / f"{slug}.md"

    def _tool_schemas(self) -> list[dict[str, Any]] | None:
        """根据 client protocol 返回对应格式的工具 schema 列表。"""
        if self._tool_registry is None:
            return None
        protocol = self._client.protocol
        if protocol in ("openai", "openai-compat"):
            schemas = build_openai_tools(self._tool_registry.list_tools())
        else:
            schemas = build_anthropic_tools(self._tool_registry.list_tools())
        return schemas or None

    async def _execute_tool(
        self, tc: ToolCallComplete
    ) -> tuple[ToolResult, float, bool]:
        """执行单个工具调用，返回 (result, elapsed, is_unknown)。"""
        if self._tool_registry is None:
            return (
                ToolResult(output="no tool registry configured", is_error=True),
                0.0,
                False,
            )

        tool = self._tool_registry.get(tc.tool_name)
        start = time.monotonic()

        if tool is None:
            return (
                ToolResult(output=f"Error: unknown tool '{tc.tool_name}'", is_error=True),
                time.monotonic() - start,
                True,  # is_unknown
            )

        if not self._tool_registry.is_enabled(tc.tool_name):
            return (
                ToolResult(
                    output=f"Error: tool '{tc.tool_name}' is disabled", is_error=True
                ),
                time.monotonic() - start,
                False,
            )

        # Plan mode:只允许 read 工具;WriteFile/EditFile 写 plan 文件路径可放行
        if self._plan_mode and getattr(tool, "category", "read") != "read":
            target_path = tc.arguments.get("file_path", "")
            write_target_matches_plan = (
                tc.tool_name in ("WriteFile", "EditFile")
                and self._plan_path is not None
                and target_path
                and Path(target_path).resolve() == self._plan_path.resolve()
            )
            if not write_target_matches_plan:
                return (
                    ToolResult(
                        output=(
                            f"Plan mode is active: tool '{tc.tool_name}' is blocked. "
                            "Only read-only tools (ReadFile/Glob/Grep) are allowed "
                            "in plan mode, plus writing to the plan file: "
                            f"{self._plan_path}. Ask the user to run `/exit-plan` "
                            "to exit plan mode before retrying other writes."
                        ),
                        is_error=True,
                    ),
                    time.monotonic() - start,
                    False,
                )

        try:
            params = tool.params_model.model_validate(tc.arguments)
            result = await tool.execute(params)
        except ValidationError as e:
            result = ToolResult(output=f"Error: invalid arguments: {e}", is_error=True)
        except Exception as e:
            result = ToolResult(output=f"Tool execution error: {e}", is_error=True)

        # 截断:超 MAX_OUTPUT_CHARS 砍前面+尾部加标记,免得撑爆 context
        result = self._truncate_tool_result(result)

        return result, time.monotonic() - start, False

    @staticmethod
    def _truncate_tool_result(result: ToolResult) -> ToolResult:
        """工具结果超 MAX_OUTPUT_CHARS 就在尾部加 [TRUNCATED] 标记。

        LLM 看到 [TRUNCATED] 关键字就知道内容被砍,自己会看 schema 找补救方法
        (比如 ReadFile 的 offset/limit)。不写太具体的提示,让模型自己判断。
        """
        if len(result.output) <= MAX_OUTPUT_CHARS:
            return result
        kept = result.output[:MAX_OUTPUT_CHARS]
        full_size = len(result.output)
        return ToolResult(
            output=(
                f"{kept}\n\n"
                f"[TRUNCATED: shown {MAX_OUTPUT_CHARS:,}, full {full_size:,} chars]"
            ),
            is_error=result.is_error,
        )

    async def _execute_batch_parallel(
        self, calls: list[ToolCallComplete]
    ) -> list[tuple[ToolCallComplete, ToolResult, float, bool]]:
        """并发执行同一个 batch 内的所有工具调用。"""
        import asyncio

        async def run_one(tc: ToolCallComplete):
            result, elapsed, is_unknown = await self._execute_tool(tc)
            return tc, result, elapsed, is_unknown

        tasks = [run_one(tc) for tc in calls]
        return list(await asyncio.gather(*tasks))

    async def run(
        self,
        user_input: str,
        conversation: ConversationManager,
    ) -> AsyncIterator[AgentEvent]:
        conversation.add_user(user_input)

        iteration = 0
        consecutive_unknown = 0
        final_text = ""

        while True:
            iteration += 1

            # 硬上限
            if iteration > self._max_iterations:
                yield ErrorEvent(
                    message=f"Agent reached maximum iterations ({self._max_iterations})"
                )
                yield LoopComplete(total_turns=iteration, text=final_text)
                return

            # 每轮重新注入 plan mode reminder(对话历史可能会污染 LLM 判断)
            # ── 动态上下文注入点(扩展契约)────────────────────────────────
            # 当前只注入 plan reminder。将来 memory / skills / hooks /
            # CLAUDE.md 指令 等子系统落地时,就在这个 block 里 add 一个
            # conversation.add_system_reminder(<那段内容>)。
            # 注意:不要动 self._system_prompt(那会破 Anthropic prompt cache),
            # 任何会变的内容都走 conversation.add_system_reminder 这条路。
            # 详细设计见 docs/prompts-design.md。
            if self._plan_mode and self._plan_path is not None:
                work_dir_str = str(self._work_dir) if self._work_dir else None
                conversation.add_system_reminder(
                    build_plan_mode_reminder(
                        plan_path=str(self._plan_path),
                        work_dir=work_dir_str,
                    )
                )

            # 构造 LLM 响应收集器
            collector = StreamCollector()

            try:
                async for event in collector.consume(
                    self._client.stream(
                        conversation,
                        system=self._system_prompt,
                        tools=self._tool_schemas(),
                    )
                ):
                    yield event

                # 从收集器取 tool_calls
                tool_calls = collector.response.tool_calls

            except Exception as e:
                yield ErrorEvent(message=str(e))
                yield LoopComplete(total_turns=iteration, text=final_text)
                return

            # 记录 token 用量
            conversation.record_usage_anchor(
                collector.response.input_tokens,
                collector.response.output_tokens,
                collector.response.cache_read,
                collector.response.cache_creation,
            )
            yield UsageEvent(
                input_tokens=collector.response.input_tokens,
                output_tokens=collector.response.output_tokens,
                cache_read=collector.response.cache_read,
                cache_creation=collector.response.cache_creation,
            )

            # 处理 max_tokens 停止原因:将当前输出接续到下一轮
            if collector.response.stop_reason == "max_tokens":
                # 简单重试：将当前输出接续到下一轮
                if collector.response.text:
                    conversation.add_assistant_message(collector.response.text)
                    conversation.add_user_message(
                        "Output token limit hit. Resume directly where you stopped. "
                        "Do not apologize or repeat previous content."
                    )
                yield RetryEvent(reason="max_tokens continuation")
                continue

            final_text = collector.response.text

            # 无 tool_calls → 本轮结束，退出循环
            if not tool_calls:
                conv_thinking = [
                    ThinkingBlock(thinking=tb.thinking, signature=tb.signature)
                    for tb in collector.response.thinking_blocks
                ]
                conversation.add_assistant_message(
                    collector.response.text,
                    thinking_blocks=conv_thinking or None,
                )
                yield TurnComplete(turn=iteration)
                yield LoopComplete(total_turns=iteration, text=final_text)
                return

            # 有 tool_calls → 记录 assistant 回复（含 tool_uses）
            uses = [
                ToolUseBlock(
                    tool_use_id=tc.tool_id,
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                )
                for tc in tool_calls
            ]
            conv_thinking = [
                ThinkingBlock(thinking=tb.thinking, signature=tb.signature)
                for tb in collector.response.thinking_blocks
            ]
            conversation.add_assistant_message(
                collector.response.text,
                tool_uses=uses,
                thinking_blocks=conv_thinking or None,
            )

            # 执行工具分组：同一 batch 可并发，不同 batch 串行
            tool_results: list[ToolResultBlock] = []
            batches = partition_tool_calls(tool_calls, self._tool_registry)

            for batch in batches:
                if batch.concurrent and len(batch.calls) > 1:
                    # 并发执行
                    batch_results = await self._execute_batch_parallel(batch.calls)
                    for tc, result, elapsed, is_unknown in batch_results:
                        if is_unknown:
                            consecutive_unknown += 1
                        else:
                            consecutive_unknown = 0

                        block = ToolResultBlock(
                            tool_use_id=tc.tool_id,
                            content=result.output,
                            is_error=result.is_error,
                        )
                        tool_results.append(block)
                        yield ToolResultEvent(
                            tool_id=tc.tool_id,
                            tool_name=tc.tool_name,
                            output=result.output,
                            is_error=result.is_error,
                            elapsed=elapsed,
                        )
                else:
                    # 串行执行
                    for tc in batch.calls:
                        result, elapsed, is_unknown = await self._execute_tool(tc)
                        if is_unknown:
                            consecutive_unknown += 1
                        else:
                            consecutive_unknown = 0

                        block = ToolResultBlock(
                            tool_use_id=tc.tool_id,
                            content=result.output,
                            is_error=result.is_error,
                        )
                        tool_results.append(block)
                        yield ToolResultEvent(
                            tool_id=tc.tool_id,
                            tool_name=tc.tool_name,
                            output=result.output,
                            is_error=result.is_error,
                            elapsed=elapsed,
                        )

            # 连续未知工具超过 3 次 → 退出
            if consecutive_unknown >= 3:
                yield ErrorEvent(
                    message="Agent terminated: too many consecutive unknown tool calls"
                )
                yield LoopComplete(total_turns=iteration, text=final_text)
                return

            # 将 tool results 写回对话，进入下一轮
            conversation.add_tool_results_message(tool_results)
            yield TurnComplete(turn=iteration)

    async def run_to_completion(
        self,
        user_input: str,
        conversation: ConversationManager,
    ) -> str:
        result = ""
        async for event in self.run(user_input, conversation):
            if isinstance(event, LoopComplete):
                result = event.text
            elif isinstance(event, ErrorEvent):
                raise RuntimeError(event.message)
        return result
