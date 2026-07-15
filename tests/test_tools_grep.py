"""Grep 工具测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from archcode.tools.grep import Grep


@pytest.mark.asyncio
async def test_grep_matches_pattern(tmp_path: Path) -> None:
    """grep 找到正则匹配的行,返回 file:line:content。"""
    (tmp_path / "a.py").write_text("hello\nworld\nhello world\n")

    tool = Grep(work_dir=tmp_path)
    result = await tool.execute(tool.Params(pattern="hello", path="."))

    assert result.is_error is False
    lines = result.output.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("a.py:1:")
    assert lines[1].startswith("a.py:3:")


@pytest.mark.asyncio
async def test_grep_include_filter(tmp_path: Path) -> None:
    """include 参数限制文件名匹配。"""
    (tmp_path / "a.py").write_text("foo\n")
    (tmp_path / "b.txt").write_text("foo\n")

    tool = Grep(work_dir=tmp_path)
    result = await tool.execute(tool.Params(pattern="foo", include="*.py"))

    assert result.is_error is False
    assert "a.py:1:" in result.output
    assert "b.txt" not in result.output


@pytest.mark.asyncio
async def test_grep_invalid_regex(tmp_path: Path) -> None:
    """无效正则报错。"""
    tool = Grep(work_dir=tmp_path)
    result = await tool.execute(tool.Params(pattern="[unclosed"))

    assert result.is_error is True
    assert "invalid regex" in result.output


@pytest.mark.asyncio
async def test_grep_no_match(tmp_path: Path) -> None:
    """没找到时返回友好提示。"""
    (tmp_path / "a.py").write_text("hello\n")

    tool = Grep(work_dir=tmp_path)
    result = await tool.execute(tool.Params(pattern="xyz"))

    assert result.is_error is False
    assert "No matches found" in result.output


@pytest.mark.asyncio
async def test_grep_skips_skip_dirs(tmp_path: Path) -> None:
    """跳过 SKIP_DIRS。"""
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "a.py").write_text("foo\n")
    (tmp_path / "main.py").write_text("foo\n")

    tool = Grep(work_dir=tmp_path)
    result = await tool.execute(tool.Params(pattern="foo"))

    assert result.is_error is False
    assert "main.py" in result.output
    assert ".venv" not in result.output


@pytest.mark.asyncio
async def test_grep_path_not_found(tmp_path: Path) -> None:
    """基础路径不存在报错。"""
    tool = Grep(work_dir=tmp_path)
    result = await tool.execute(tool.Params(pattern="foo", path="nope"))

    assert result.is_error is True
    assert "path not found" in result.output


def test_grep_is_concurrency_safe() -> None:
    """Grep 只读,可并发。"""
    assert Grep(work_dir=Path("/")).is_concurrency_safe is True


def test_grep_category_is_read() -> None:
    """Grep category 是 read。"""
    assert Grep(work_dir=Path("/")).category == "read"