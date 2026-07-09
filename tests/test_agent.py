from __future__ import annotations

from typing import AsyncIterator

import pytest

from archcode.agent import Agent, LoopComplete, StreamText
from archcode.client import LLMClient, StreamEnd, StreamEvent, TextDelta
from archcode.conversation import ConversationManager


class MockClient(LLMClient):
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def stream(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        text = self._responses.pop(0)
        yield TextDelta(text=text)
        yield StreamEnd(text=text)


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


@pytest.mark.asyncio
async def test_conversation_clear() -> None:
    conv = ConversationManager()
    conv.add_user("a")
    conv.add_assistant("b")
    conv.clear()
    assert conv.history == []
