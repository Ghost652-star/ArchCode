"""EditFile 工具测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from archcode.tools.edit_file import EditFile


@pytest.mark.asyncio
async def test_edit_file_replaces_unique_string(tmp_path: Path) -> None:
    """old_string 唯一时成功替换。"""
    (tmp_path / "f.txt").write_text("hello world\n")

    tool = EditFile(work_dir=tmp_path)
    result = await tool.execute(
        tool.Params(file_path="f.txt", old_string="hello world", new_string="hi there")
    )

    assert result.is_error is False
    assert (tmp_path / "f.txt").read_text() == "hi there\n"


@pytest.mark.asyncio
async def test_edit_file_old_string_not_found(tmp_path: Path) -> None:
    """old_string 不存在报错。"""
    (tmp_path / "f.txt").write_text("hello")

    tool = EditFile(work_dir=tmp_path)
    result = await tool.execute(
        tool.Params(file_path="f.txt", old_string="xyz", new_string="new")
    )

    assert result.is_error is True
    assert "not found" in result.output


@pytest.mark.asyncio
async def test_edit_file_old_string_not_unique(tmp_path: Path) -> None:
    """old_string 出现多次报错(要求唯一)。"""
    (tmp_path / "f.txt").write_text("foo foo foo")

    tool = EditFile(work_dir=tmp_path)
    result = await tool.execute(
        tool.Params(file_path="f.txt", old_string="foo", new_string="bar")
    )

    assert result.is_error is True
    assert "must be unique" in result.output


@pytest.mark.asyncio
async def test_edit_file_file_not_found(tmp_path: Path) -> None:
    """文件不存在报错。"""
    tool = EditFile(work_dir=tmp_path)
    result = await tool.execute(
        tool.Params(file_path="nonexistent.txt", old_string="x", new_string="y")
    )

    assert result.is_error is True
    assert "file not found" in result.output


def test_edit_file_category_is_write() -> None:
    """EditFile category 是 write。"""
    assert EditFile(work_dir=Path("/")).category == "write"