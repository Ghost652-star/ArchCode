"""ReadFile 工具测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from archcode.tools.read_file import ReadFile


@pytest.mark.asyncio
async def test_read_file_returns_numbered_lines(tmp_path: Path) -> None:
    """读文件返回带行号的内容。"""
    (tmp_path / "test.txt").write_text("alpha\nbeta\ngamma\n")

    tool = ReadFile(work_dir=tmp_path)
    result = await tool.execute(tool.Params(file_path="test.txt"))

    assert result.is_error is False
    lines = result.output.splitlines()
    assert lines == ["1\talpha", "2\tbeta", "3\tgamma"]


@pytest.mark.asyncio
async def test_read_file_offset_and_limit(tmp_path: Path) -> None:
    """offset/limit 截断读取。"""
    (tmp_path / "f.txt").write_text("\n".join(f"line{i}" for i in range(10)))

    tool = ReadFile(work_dir=tmp_path)
    result = await tool.execute(tool.Params(file_path="f.txt", offset=3, limit=2))

    assert result.is_error is False
    assert result.output.splitlines() == ["4\tline3", "5\tline4"]


@pytest.mark.asyncio
async def test_read_file_not_found(tmp_path: Path) -> None:
    """文件不存在报错。"""
    tool = ReadFile(work_dir=tmp_path)
    result = await tool.execute(tool.Params(file_path="nonexistent.txt"))

    assert result.is_error is True
    assert "file not found" in result.output


@pytest.mark.asyncio
async def test_read_file_path_is_directory(tmp_path: Path) -> None:
    """路径是目录时报错。"""
    (tmp_path / "subdir").mkdir()

    tool = ReadFile(work_dir=tmp_path)
    result = await tool.execute(tool.Params(file_path="subdir"))

    assert result.is_error is True
    assert "not a file" in result.output


@pytest.mark.asyncio
async def test_read_file_relative_path(tmp_path: Path) -> None:
    """相对路径基于 work_dir。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "f.txt").write_text("content\n")

    tool = ReadFile(work_dir=tmp_path)
    result = await tool.execute(tool.Params(file_path="sub/f.txt"))

    assert result.is_error is False
    assert result.output == "1\tcontent"


def test_read_file_is_concurrency_safe() -> None:
    """ReadFile 只读,可并发。"""
    assert ReadFile(work_dir=Path("/")).is_concurrency_safe is True


def test_read_file_category_is_read() -> None:
    """ReadFile category 是 read。"""
    assert ReadFile(work_dir=Path("/")).category == "read"