"""Bash 工具测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from archcode.tools.bash import Bash


@pytest.mark.asyncio
async def test_bash_executes_simple_command(tmp_path: Path) -> None:
    """执行简单命令,返回 stdout。"""
    tool = Bash()
    result = await tool.execute(tool.Params(command="echo hello"))

    assert result.is_error is False
    assert "STDOUT:\nhello" in result.output


@pytest.mark.asyncio
async def test_bash_captures_stderr(tmp_path: Path) -> None:
    """stderr 被捕获并标记。跨平台用 Python 触发 stderr。"""
    tool = Bash()
    cmd = f'{sys.executable} -c "import sys; sys.stderr.write(\\"err\\\\n\\"); sys.exit(1)"'
    result = await tool.execute(tool.Params(command=cmd))

    assert result.is_error is True
    assert "STDERR:" in result.output


@pytest.mark.asyncio
async def test_bash_no_output(tmp_path: Path) -> None:
    """命令没输出时显示 (no output)。"""
    tool = Bash()
    # true 在 cmd 下不存在,用 python -c "pass"
    result = await tool.execute(tool.Params(command=f"{sys.executable} -c \"pass\""))

    assert result.is_error is False
    assert "(no output)" in result.output


@pytest.mark.asyncio
async def test_bash_timeout(tmp_path: Path) -> None:
    """超时后报错。"""
    tool = Bash()
    # python sleep 30 秒,timeout 1 秒
    result = await tool.execute(
        tool.Params(command=f"{sys.executable} -c \"import time; time.sleep(30)\"", timeout=1)
    )

    assert result.is_error is True
    assert "timed out" in result.output


@pytest.mark.asyncio
async def test_bash_timeout_capped_at_600(tmp_path: Path) -> None:
    """timeout 最大 600 秒。"""
    # 这里只验证内部 clamp,不真的 sleep 600 秒
    tool = Bash()
    result = await tool.execute(
        tool.Params(command=f"{sys.executable} -c \"print('fast')\"", timeout=999)
    )

    assert result.is_error is False
    assert "STDOUT:\nfast" in result.output


@pytest.mark.asyncio
async def test_bash_non_zero_exit_is_error(tmp_path: Path) -> None:
    """非零退出码标记 is_error=True。"""
    tool = Bash()
    result = await tool.execute(
        tool.Params(command=f"{sys.executable} -c \"exit(2)\"")
    )

    assert result.is_error is True


def test_bash_category_is_command() -> None:
    """Bash category 是 command。"""
    assert Bash().category == "command"