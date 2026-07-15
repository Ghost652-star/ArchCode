"""WriteFile 工具测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from archcode.tools.write_file import WriteFile


@pytest.mark.asyncio
async def test_write_file_creates_file(tmp_path: Path) -> None:
    """写入新文件。"""
    tool = WriteFile(work_dir=tmp_path)
    result = await tool.execute(tool.Params(file_path="new.txt", content="hello\n"))

    assert result.is_error is False
    assert (tmp_path / "new.txt").read_text() == "hello\n"


@pytest.mark.asyncio
async def test_write_file_overwrites(tmp_path: Path) -> None:
    """覆盖已有文件。"""
    (tmp_path / "f.txt").write_text("old")

    tool = WriteFile(work_dir=tmp_path)
    result = await tool.execute(tool.Params(file_path="f.txt", content="new"))

    assert result.is_error is False
    assert (tmp_path / "f.txt").read_text() == "new"


@pytest.mark.asyncio
async def test_write_file_creates_parent_dirs(tmp_path: Path) -> None:
    """自动创建父目录。"""
    tool = WriteFile(work_dir=tmp_path)
    result = await tool.execute(tool.Params(file_path="a/b/c.txt", content="x"))

    assert result.is_error is False
    assert (tmp_path / "a" / "b" / "c.txt").read_text() == "x"


@pytest.mark.asyncio
async def test_write_file_relative_path(tmp_path: Path) -> None:
    """相对路径基于 work_dir。"""
    (tmp_path / "sub").mkdir()

    tool = WriteFile(work_dir=tmp_path)
    result = await tool.execute(tool.Params(file_path="sub/x.txt", content="hi"))

    assert result.is_error is False
    assert (tmp_path / "sub" / "x.txt").read_text() == "hi"


def test_write_file_category_is_write() -> None:
    """WriteFile category 是 write。"""
    assert WriteFile(work_dir=Path("/")).category == "write"


def test_write_file_not_concurrency_safe_by_default() -> None:
    """WriteFile 默认不能并发。"""
    assert WriteFile(work_dir=Path("/")).is_concurrency_safe is False