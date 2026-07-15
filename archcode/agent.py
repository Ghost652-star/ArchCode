from __future__ import annotations

import time
from dataclasses import dataclass
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
    ToolCallStart,
)
from archcode.llm.serializer import build_anthropic_tools, build_openai_tools
from archcode.tools.base import ToolResult
from archcode.tools.registry import ToolRegistry


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
    """工具执行结果事件(发往 UI 显示)。"""

    tool_id: str
    tool_name: str
    output: str
    is_error: bool
    elapsed: float


@dataclass
class TurnComplete:
    text: str


@dataclass
class ErrorEvent:
    message: str


@dataclass
class LoopComplete:
    text: str


@dataclass
class UsageEvent:
    input_tokens: int
    output_tokens: int
    cache_read: int = 0
    cache_creation: int = 0


AgentEvent = (
    StreamText
    | ThinkingText
    | ToolUseEvent
    | ToolResultEvent
    | TurnComplete
    | ErrorEvent
    | LoopComplete
    | UsageEvent
)


class Agent:
    """Agent 循环:用户消息 → LLM 流式事件 → 写入历史。

    v0.2 单步工具执行:
    - Turn 1:用户输入 → stream → 如果模型请求工具,执行 + 回灌结果
    - Turn 2:重调一次 stream,让模型看 ToolResultBlock → 模型总结
    - max_iterations=2 写死(即使 Turn 2 又请求工具,也不执行)
    - 后续 v0.3 改 max_iterations=∞ + end_turn 判断,支持完整 ReAct
    """

    def __init__(
        self,
        client: LLMClient,
        system_prompt: str,
        tool_registry: ToolRegistry | None = None,
        max_output_tokens: int = 4096,
        max_iterations: int = 2,
    ) -> None:
        self._client = client
        self._system_prompt = system_prompt
        self._tool_registry = tool_registry
        self._max_iterations = max_iterations
        self._client.set_max_output_tokens(max_output_tokens)

    def _get_tool_schemas(self) -> list[dict[str, Any]] | None:
        """根据 client protocol 返回对应格式的工具 schema 列表。"""
        if self._tool_registry is None:
            return None
        protocol = self._client.protocol
        if protocol in ("openai", "openai-compat"):
            schemas = build_openai_tools(self._tool_registry.list_tools())
        else:
            schemas = build_anthropic_tools(self._tool_registry.list_tools())
        return schemas or None

    async def _execute_tool(self, tc: ToolUseBlock) -> ToolResult:
        """执行单个工具调用,失败统一返回 is_error=True 的 ToolResult。"""
        if self._tool_registry is None:
            return ToolResult(output="no tool registry configured", is_error=True)
        tool = self._tool_registry.get(tc.tool_name)
        if tool is None:
            return ToolResult(output=f"Error: unknown tool '{tc.tool_name}'", is_error=True)
        if not self._tool_registry.is_enabled(tc.tool_name):
            return ToolResult(
                output=f"Error: tool '{tc.tool_name}' is disabled", is_error=True
            )
        try:
            params = tool.params_model.model_validate(tc.arguments)
        except ValidationError as e:
            return ToolResult(output=f"Error: invalid arguments: {e}", is_error=True)
        return await tool.execute(params)

    async def run(
        self,
        user_input: str,
        conversation: ConversationManager,
    ) -> AsyncIterator[AgentEvent]:
        conversation.add_user(user_input)

        text = ""
        for iteration in range(self._max_iterations):
            full_response: list[str] = []
            tool_uses: list[ToolUseBlock] = []
            thinking_blocks: list[ThinkingBlock] = []

            try:
                async for event in self._client.stream(
                    conversation,
                    system=self._system_prompt,
                    tools=self._get_tool_schemas(),
                ):
                    if isinstance(event, TextDelta):
                        full_response.append(event.text)
                        yield StreamText(text=event.text)
                    elif isinstance(event, ThinkingDelta):
                        yield ThinkingText(text=event.text)
                    elif isinstance(event, ThinkingComplete):
                        thinking_blocks.append(
                            ThinkingBlock(
                                thinking=event.thinking,
                                signature=event.signature,
                            )
                        )
                    elif isinstance(event, ToolCallStart):
                        pass
                    elif isinstance(event, ToolCallComplete):
                        tool_uses.append(
                            ToolUseBlock(
                                tool_use_id=event.tool_id,
                                tool_name=event.tool_name,
                                arguments=event.arguments,
                            )
                        )
                        yield ToolUseEvent(
                            tool_name=event.tool_name,
                            tool_id=event.tool_id,
                            arguments=event.arguments,
                        )
                    elif isinstance(event, StreamEnd):
                        conversation.record_usage_anchor(
                            event.input_tokens,
                            event.output_tokens,
                            event.cache_read,
                            event.cache_creation,
                        )
                        yield UsageEvent(
                            input_tokens=event.input_tokens,
                            output_tokens=event.output_tokens,
                            cache_read=event.cache_read,
                            cache_creation=event.cache_creation,
                        )
            except Exception as e:
                yield ErrorEvent(message=str(e))
                return

            text = "".join(full_response)
            conversation.add_assistant(
                text,
                tool_uses=tool_uses or None,
                thinking_blocks=thinking_blocks or None,
            )
            yield TurnComplete(text=text)

            # 没有工具调用 → 退出循环
            if not tool_uses:
                break

            # v0.2 单步:只在第一次 iteration 执行工具。后续 iteration 即使
            # 模型又请求工具,也不执行,直接退出(留给 v0.3 改 ReAct 时调整)
            if iteration == 0:
                # 执行所有工具调用(顺序,不分并发)
                for tc in tool_uses:
                    started = time.monotonic()
                    result = await self._execute_tool(tc)
                    elapsed = time.monotonic() - started
                    # 关键:id 必须严格用 tc.tool_use_id,禁止合成
                    conversation.add_tool_results(
                        [
                            ToolResultBlock(
                                tool_use_id=tc.tool_use_id,
                                content=result.output,
                                is_error=result.is_error,
                            )
                        ]
                    )
                    yield ToolResultEvent(
                        tool_id=tc.tool_use_id,
                        tool_name=tc.tool_name,
                        output=result.output,
                        is_error=result.is_error,
                        elapsed=elapsed,
                    )
            else:
                # Turn 2/3+ 又请求工具,不再执行,直接退出
                break

        yield LoopComplete(text=text)

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