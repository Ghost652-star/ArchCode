from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from archcode.agent import Agent, LoopComplete, StreamText
from archcode.conversation.manager import ConversationManager
from archcode.conversation.models import Message, ToolUseBlock
from archcode.llm.client import LLMClient
from archcode.llm.events import StreamEnd, StreamEvent, TextDelta
from archcode.llm.serializer import (
    build_anthropic_messages,
    build_chat_completion_messages,
)


class MockClient(LLMClient):
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        text = self._responses.pop(0)
        yield TextDelta(text=text)
        yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=5)


@pytest.mark.asyncio
async def test_agent_basic_reply() -> None:
    client = MockClient(["Hello!"])
    agent = Agent(client=client, system_prompt="You are helpful.")
    conv = ConversationManager()

    events = []
    async for event in agent.run("Hi", conv):
        events.append(event)

    assert any(isinstance(e, StreamText) and e.text == "Hello!" for e in events)
    assert any(isinstance(e, LoopComplete) and e.text == "Hello!" for e in events)
    assert len(conv.history) == 2
    assert conv.history[0].role == "user"
    assert conv.history[1].role == "assistant"
    assert conv.baseline_tokens == 15


@pytest.mark.asyncio
async def test_conversation_clear() -> None:
    conv = ConversationManager()
    conv.add_user("a")
    conv.add_assistant("b")
    conv.clear()
    assert conv.history == []


def test_serialize_chat_completion_with_tools() -> None:
    messages = [
        Message(role="user", content="read file"),
        Message(
            role="assistant",
            content="",
            tool_uses=[
                ToolUseBlock(
                    tool_use_id="call_1",
                    tool_name="ReadFile",
                    arguments={"path": "a.py"},
                )
            ],
        ),
    ]
    out = build_chat_completion_messages(messages)
    assert out[1]["tool_calls"][0]["function"]["name"] == "ReadFile"


def test_serialize_anthropic_with_tools() -> None:
    messages = [
        Message(
            role="assistant",
            content="ok",
            tool_uses=[
                ToolUseBlock(
                    tool_use_id="tu_1",
                    tool_name="Bash",
                    arguments={"command": "ls"},
                )
            ],
        )
    ]
    out = build_anthropic_messages(messages)
    assert out[0]["content"][1]["type"] == "tool_use"
    assert out[0]["content"][1]["name"] == "Bash"
