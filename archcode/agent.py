from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from archcode.client import LLMClient, StreamEnd, TextDelta
from archcode.conversation import ConversationManager


@dataclass
class StreamText:
    text: str


@dataclass
class TurnComplete:
    text: str


@dataclass
class ErrorEvent:
    message: str


@dataclass
class LoopComplete:
    text: str


AgentEvent = StreamText | TurnComplete | ErrorEvent | LoopComplete


class Agent:
    """最小 Agent 循环：用户消息 → LLM 流式回复 → 写入历史。

    后续扩展点：
    - 工具调用（tools/）
    - 权限检查（permissions/）
    - 上下文压缩（context/）
    - Hooks 事件（hooks/）
    """

    def __init__(
        self,
        client: LLMClient,
        system_prompt: str,
        max_output_tokens: int = 4096,
    ) -> None:
        self._client = client
        self._system_prompt = system_prompt
        self._max_output_tokens = max_output_tokens

    async def run(
        self,
        user_input: str,
        conversation: ConversationManager,
    ) -> AsyncIterator[AgentEvent]:
        conversation.add_user(user_input)
        messages = conversation.to_api_messages(self._system_prompt)

        full_response: list[str] = []
        try:
            async for event in self._client.stream(messages, self._max_output_tokens):
                if isinstance(event, TextDelta):
                    full_response.append(event.text)
                    yield StreamText(text=event.text)
                elif isinstance(event, StreamEnd):
                    if event.text and not full_response:
                        full_response.append(event.text)
        except Exception as e:
            yield ErrorEvent(message=str(e))
            return

        text = "".join(full_response)
        conversation.add_assistant(text)
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
