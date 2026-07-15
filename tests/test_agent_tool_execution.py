"""Agent 单步工具执行测试。

覆盖 spec §4.5 / §6.2 的改造点:
- 收到 ToolCallComplete → 执行工具 → 写 ToolResultBlock → 重调 stream
- max_iterations=2 时 Turn 2 又请求工具 → 不执行,直接退出
- 工具不存在 / 参数校验失败 / 工具被禁用 → 返回 is_error=True
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from pydantic import BaseModel, Field

from archcode.agent import Agent, LoopComplete, StreamText, ToolResultEvent, TurnComplete
from archcode.conversation.manager import ConversationManager
from archcode.llm.client import LLMClient
from archcode.llm.events import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCallComplete,
    ToolCallStart,
)
from archcode.tools.base import Tool, ToolResult
from archcode.tools.registry import ToolRegistry


class EchoParams(BaseModel):
    text: str = Field(description="text to echo back")


class EchoTool(Tool):
    name = "Echo"
    description = "Echo input text back."
    category = "read"

    class Params(EchoParams):
        pass

    params_model = Params

    async def execute(self, params: Params) -> ToolResult:
        return ToolResult(output=f"echo: {params.text}")


class SequenceMockClient(LLMClient):
    """按 sequences 输出 stream 事件,每次调用取下一组。

    sequences[i] = 第一次 stream 的事件列表
    sequences[i+1] = 第二次 stream 的事件列表
    """

    protocol = "anthropic"

    def __init__(self, sequences: list[list[StreamEvent]]) -> None:
        self._sequences = [list(s) for s in sequences]
        self.stream_call_count = 0
        self.last_tools_param: list[dict[str, Any]] | None = None

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.stream_call_count += 1
        self.last_tools_param = tools
        events = self._sequences[self.stream_call_count - 1]
        for e in events:
            yield e


def make_tool_call(name: str, args: dict, call_id: str = "call_1") -> list[StreamEvent]:
    """构造一组 ToolCallStart + ToolCallComplete + StreamEnd 事件。"""
    return [
        ToolCallStart(tool_name=name, tool_id=call_id),
        ToolCallComplete(tool_id=call_id, tool_name=name, arguments=args),
        StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=5),
    ]


def make_text_response(text: str) -> list[StreamEvent]:
    """构造一组 TextDelta + StreamEnd 事件。"""
    return [
        TextDelta(text=text),
        StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=5),
    ]


@pytest.mark.asyncio
async def test_agent_executes_tool_and_feeds_back(tmp_path: Path) -> None:
    """Turn 1 请求工具 → 执行 → 写 ToolResultBlock → 重调 stream 一次。"""
    registry = ToolRegistry()
    registry.register(EchoTool())

    # 第一次 stream:模型请求 Echo 工具
    # 第二次 stream:模型看到工具结果,返回最终答复
    client = SequenceMockClient([
        make_tool_call("Echo", {"text": "hello"}, "call_echo"),
        make_text_response("done!"),
    ])
    agent = Agent(
        client=client,
        system_prompt="test",
        tool_registry=registry,
        max_iterations=2,
    )
    conv = ConversationManager()

    events = []
    async for event in agent.run("call Echo", conv):
        events.append(event)

    # stream 被调了 2 次(Turn 1 + Turn 2)
    assert client.stream_call_count == 2

    # 至少有一个 ToolResultEvent
    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 1
    assert tool_results[0].tool_name == "Echo"
    assert tool_results[0].is_error is False
    assert "echo: hello" in tool_results[0].output
    assert tool_results[0].elapsed >= 0

    # history 里应该有 user + assistant(tool_uses) + user(tool_results) + assistant(text)
    # = 4 条
    assert len(conv.history) == 4
    assert conv.history[1].tool_uses[0].tool_name == "Echo"
    assert conv.history[2].tool_results[0].content == "echo: hello"
    assert conv.history[2].tool_results[0].is_error is False
    # 关键的 id 配对:tool_use_id == "call_echo"
    assert conv.history[2].tool_results[0].tool_use_id == "call_echo"

    # 最终 LoopComplete 包含 Turn 2 的文本
    loop_events = [e for e in events if isinstance(e, LoopComplete)]
    assert loop_events[0].text == "done!"


@pytest.mark.asyncio
async def test_agent_no_tool_call_ends_after_one_turn(tmp_path: Path) -> None:
    """模型不调用工具,Turn 1 直接结束,stream 只调一次。"""
    client = SequenceMockClient([make_text_response("hi")])
    agent = Agent(
        client=client,
        system_prompt="test",
        tool_registry=ToolRegistry(),
        max_iterations=2,
    )
    conv = ConversationManager()

    async for _ in agent.run("hello", conv):
        pass

    assert client.stream_call_count == 1
    assert len(conv.history) == 2  # user + assistant


@pytest.mark.asyncio
async def test_agent_max_iterations_2_does_not_execute_in_turn_2(tmp_path: Path) -> None:
    """max_iterations=2 时 Turn 2 又请求工具,不执行,直接退出。"""
    registry = ToolRegistry()
    registry.register(EchoTool())

    # 第一次 + 第二次都请求工具
    client = SequenceMockClient([
        make_tool_call("Echo", {"text": "first"}, "call_1"),
        make_tool_call("Echo", {"text": "second"}, "call_2"),
    ])
    agent = Agent(
        client=client,
        system_prompt="test",
        tool_registry=registry,
        max_iterations=2,
    )
    conv = ConversationManager()

    events = []
    async for event in agent.run("test", conv):
        events.append(event)

    # stream 调了 2 次(到 max_iterations 上限)
    assert client.stream_call_count == 2

    # 只有 Turn 1 的工具被执行(1 个 ToolResultEvent)
    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 1
    assert tool_results[0].tool_id == "call_1"
    assert "echo: first" in tool_results[0].output


@pytest.mark.asyncio
async def test_agent_unknown_tool_returns_error(tmp_path: Path) -> None:
    """模型请求未注册的工具 → 返回 is_error=True 的 ToolResult。"""
    registry = ToolRegistry()  # 空 registry

    client = SequenceMockClient([
        make_tool_call("UnknownTool", {}, "call_x"),
        make_text_response("ok"),
    ])
    agent = Agent(
        client=client,
        system_prompt="test",
        tool_registry=registry,
        max_iterations=2,
    )
    conv = ConversationManager()

    events = []
    async for event in agent.run("test", conv):
        events.append(event)

    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is True
    assert "unknown tool" in tool_results[0].output


@pytest.mark.asyncio
async def test_agent_disabled_tool_returns_error(tmp_path: Path) -> None:
    """工具被 disable → 返回 is_error=True。"""
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.disable("Echo")

    client = SequenceMockClient([
        make_tool_call("Echo", {"text": "x"}, "call_x"),
        make_text_response("ok"),
    ])
    agent = Agent(
        client=client,
        system_prompt="test",
        tool_registry=registry,
        max_iterations=2,
    )
    conv = ConversationManager()

    events = []
    async for event in agent.run("test", conv):
        events.append(event)

    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert tool_results[0].is_error is True
    assert "disabled" in tool_results[0].output


@pytest.mark.asyncio
async def test_agent_invalid_args_returns_error(tmp_path: Path) -> None:
    """模型给的参数校验失败 → 返回 is_error=True。"""
    registry = ToolRegistry()
    registry.register(EchoTool())

    # 缺 text 字段
    client = SequenceMockClient([
        make_tool_call("Echo", {}, "call_x"),
        make_text_response("ok"),
    ])
    agent = Agent(
        client=client,
        system_prompt="test",
        tool_registry=registry,
        max_iterations=2,
    )
    conv = ConversationManager()

    events = []
    async for event in agent.run("test", conv):
        events.append(event)

    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert tool_results[0].is_error is True
    assert "invalid arguments" in tool_results[0].output


@pytest.mark.asyncio
async def test_agent_passes_tool_schemas_to_client(tmp_path: Path) -> None:
    """Agent 调用 client.stream 时把工具 schemas 传进去。"""
    registry = ToolRegistry()
    registry.register(EchoTool())

    client = SequenceMockClient([make_text_response("ok")])
    agent = Agent(
        client=client,
        system_prompt="test",
        tool_registry=registry,
    )
    conv = ConversationManager()

    async for _ in agent.run("hello", conv):
        pass

    assert client.last_tools_param is not None
    assert len(client.last_tools_param) == 1
    assert client.last_tools_param[0]["name"] == "Echo"


@pytest.mark.asyncio
async def test_agent_no_registry_passes_none_tools(tmp_path: Path) -> None:
    """没有 registry 时,tools 参数传 None。"""
    client = SequenceMockClient([make_text_response("ok")])
    agent = Agent(client=client, system_prompt="test", tool_registry=None)
    conv = ConversationManager()

    async for _ in agent.run("hello", conv):
        pass

    assert client.last_tools_param is None


@pytest.mark.asyncio
async def test_agent_tool_execution_adds_text_deltas_too(tmp_path: Path) -> None:
    """Turn 1 同时有文本和工具调用 → yield 文本 + ToolUseEvent + ToolResultEvent。"""
    registry = ToolRegistry()
    registry.register(EchoTool())

    # Turn 1: 文本 + 工具调用
    turn1 = [
        TextDelta(text="Let me echo. "),
        ToolCallStart(tool_name="Echo", tool_id="c1"),
        ToolCallComplete(tool_id="c1", tool_name="Echo", arguments={"text": "x"}),
        StreamEnd(stop_reason="tool_use", input_tokens=10, output_tokens=5),
    ]
    # Turn 2: 文本答复
    turn2 = make_text_response("done")

    client = SequenceMockClient([turn1, turn2])
    agent = Agent(
        client=client,
        system_prompt="test",
        tool_registry=registry,
        max_iterations=2,
    )
    conv = ConversationManager()

    events = []
    async for event in agent.run("test", conv):
        events.append(event)

    # Turn 1 的文本 "Let me echo. " 通过 StreamText yield
    stream_texts = [e.text for e in events if isinstance(e, StreamText)]
    assert "Let me echo. " in stream_texts
    assert "done" in stream_texts

    # 工具结果 yield 了
    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 1
    assert "echo: x" in tool_results[0].output

    # 两个 TurnComplete(Turn 1 + Turn 2)
    turn_completes = [e for e in events if isinstance(e, TurnComplete)]
    assert len(turn_completes) == 2