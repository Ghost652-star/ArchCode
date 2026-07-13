from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from archcode.conversation.manager import ConversationManager
from archcode.conversation.models import ThinkingBlock, ToolUseBlock
from archcode.llm.client import LLMClient
from archcode.llm.events import (
    StreamEnd,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallStart,
)


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
    | TurnComplete
    | ErrorEvent
    | LoopComplete
    | UsageEvent
)


class Agent:
    """Agent 循环：用户消息 → LLM 流式事件 → 写入历史。

    当前先跑通文本 + thinking 展示；收到 ToolCallComplete 时记录到消息里，
    完整「执行工具 → 回灌结果」等 tools/ 模块就绪后再接。
    """

    def __init__(
        self,
        client: LLMClient,
        system_prompt: str,
        max_output_tokens: int = 4096,
    ) -> None:
        self._client = client
        self._system_prompt = system_prompt
        self._client.set_max_output_tokens(max_output_tokens)

    async def run(
        self,
        user_input: str,
        conversation: ConversationManager,
    ) -> AsyncIterator[AgentEvent]:
        conversation.add_user(user_input)

        full_response: list[str] = []
        tool_uses: list[ToolUseBlock] = []
        thinking_blocks: list[ThinkingBlock] = []

        try:
            async for event in self._client.stream(
                conversation,
                system=self._system_prompt,
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
