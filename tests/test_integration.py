"""端到端集成测试:mock LLM + 真实工具执行。

验证 Agent.run() → tool_registry.get() → tool.execute() → ToolResultBlock →
再 stream → LoopComplete 这条完整链路。

不消耗 API 配额,但跑真实的 ReadFile / Bash 等工具。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from archcode.agent import Agent, ToolResultEvent
from archcode.conversation.manager import ConversationManager
from archcode.llm.client import LLMClient
from archcode.llm.events import StreamEnd, StreamEvent, TextDelta, ToolCallComplete, ToolCallStart
from archcode.tools import create_default_registry


class LLMCallsReadFile(LLMClient):
    """模拟 LLM:第一次请求 ReadFile 读 agent.py,第二次返回总结。"""

    protocol = "anthropic"

    def __init__(self, file_to_read: str) -> None:
        self._file = file_to_read
        self.call_count = 0

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.call_count += 1
        if self.call_count == 1:
            # 请求 ReadFile
            yield ToolCallStart(tool_name="ReadFile", tool_id="r1")
            yield ToolCallComplete(
                tool_id="r1",
                tool_name="ReadFile",
                arguments={"file_path": self._file},
            )
            yield StreamEnd(stop_reason="tool_use", input_tokens=10, output_tokens=5)
        else:
            # Turn 2:看到工具结果,总结答复
            yield TextDelta(text=f"Read {self._file} successfully.")
            yield StreamEnd(stop_reason="end_turn", input_tokens=20, output_tokens=10)


@pytest.mark.asyncio
async def test_end_to_end_read_real_file(tmp_path: Path) -> None:
    """真实跑 ReadFile 读文件 → 写回 → 重调 stream → 总结。"""
    target = tmp_path / "agent.py"
    target.write_text("#!/usr/bin/env python\nprint('hello')\n")

    work_dir = tmp_path
    registry = create_default_registry(work_dir=work_dir)

    client = LLMCallsReadFile("agent.py")
    agent = Agent(
        client=client,
        system_prompt="test",
        tool_registry=registry,
        max_iterations=2,
    )
    conv = ConversationManager()

    events = []
    async for event in agent.run("看下 agent.py", conv):
        events.append(event)

    # stream 调了 2 次(Turn 1 请求工具 + Turn 2 总结)
    assert client.call_count == 2

    # 工具结果事件:output 应该是文件实际内容
    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is False
    assert "print('hello')" in tool_results[0].output
    assert "1\t" in tool_results[0].output  # 行号格式

    # history 里应该有 4 条:user, assistant(tool_uses), user(tool_results), assistant(text)
    assert len(conv.history) == 4
    assert conv.history[1].tool_uses[0].tool_name == "ReadFile"
    assert conv.history[2].tool_results[0].is_error is False
    assert "print('hello')" in conv.history[2].tool_results[0].content


class LLMCallsBash(LLMClient):
    """模拟 LLM:请求 Bash 跑命令。"""

    protocol = "anthropic"

    def __init__(self, command: str) -> None:
        self._command = command
        self.call_count = 0

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.call_count += 1
        if self.call_count == 1:
            yield ToolCallStart(tool_name="Bash", tool_id="b1")
            yield ToolCallComplete(
                tool_id="b1",
                tool_name="Bash",
                arguments={"command": self._command},
            )
            yield StreamEnd(stop_reason="tool_use", input_tokens=10, output_tokens=5)
        else:
            yield TextDelta(text="done")
            yield StreamEnd(stop_reason="end_turn", input_tokens=20, output_tokens=3)


@pytest.mark.asyncio
async def test_end_to_end_bash_runs_real_command(tmp_path: Path) -> None:
    """真实跑 Bash 跑 python 命令。"""
    registry = create_default_registry(work_dir=tmp_path)
    client = LLMCallsBash(f'{sys.executable} -c "print(2+2)"')
    agent = Agent(
        client=client,
        system_prompt="test",
        tool_registry=registry,
        max_iterations=2,
    )
    conv = ConversationManager()

    events = []
    async for event in agent.run("calc", conv):
        events.append(event)

    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is False
    assert "STDOUT:\n4" in tool_results[0].output


class LLMCallsGlob(LLMClient):
    """模拟 LLM:请求 Glob。"""

    protocol = "anthropic"

    def __init__(self, pattern: str) -> None:
        self._pattern = pattern
        self.call_count = 0

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.call_count += 1
        if self.call_count == 1:
            yield ToolCallStart(tool_name="Glob", tool_id="g1")
            yield ToolCallComplete(
                tool_id="g1",
                tool_name="Glob",
                arguments={"pattern": self._pattern},
            )
            yield StreamEnd(stop_reason="tool_use", input_tokens=10, output_tokens=5)
        else:
            yield TextDelta(text="found")
            yield StreamEnd(stop_reason="end_turn", input_tokens=20, output_tokens=3)


@pytest.mark.asyncio
async def test_end_to_end_glob_finds_files(tmp_path: Path) -> None:
    """真实跑 Glob 找文件。"""
    (tmp_path / "a.py").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "c.py").write_text("c")

    registry = create_default_registry(work_dir=tmp_path)
    client = LLMCallsGlob("*.py")
    agent = Agent(
        client=client,
        system_prompt="test",
        tool_registry=registry,
        max_iterations=2,
    )
    conv = ConversationManager()

    events = []
    async for event in agent.run("find py", conv):
        events.append(event)

    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is False
    # 应该找到 a.py 和 c.py
    files = tool_results[0].output.splitlines()
    assert "a.py" in files
    assert "c.py" in files
    assert "b.txt" not in files


@pytest.mark.asyncio
async def test_create_default_registry_returns_all_six_tools(tmp_path: Path) -> None:
    """create_default_registry 返回 6 个工具。"""
    registry = create_default_registry(work_dir=tmp_path)
    names = {t.name for t in registry.list_tools()}
    assert names == {"ReadFile", "WriteFile", "EditFile", "Bash", "Glob", "Grep"}


@pytest.mark.asyncio
async def test_all_six_tools_are_enabled(tmp_path: Path) -> None:
    """默认 6 个工具都启用。"""
    registry = create_default_registry(work_dir=tmp_path)
    for name in ["ReadFile", "WriteFile", "EditFile", "Bash", "Glob", "Grep"]:
        assert registry.is_enabled(name), f"{name} should be enabled"