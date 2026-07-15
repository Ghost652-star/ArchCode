"""Glob 工具测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from archcode.tools.glob import Glob


@pytest.mark.asyncio
async def test_glob_matches_files(tmp_path: Path) -> None:
    """glob 模式匹配文件,返回相对路径。"""
    (tmp_path / "a.py").write_text("a")
    (tmp_path / "b.py").write_text("b")
    (tmp_path / "c.txt").write_text("c")

    tool = Glob(work_dir=tmp_path)
    result = await tool.execute(tool.Params(pattern="*.py"))

    assert result.is_error is False
    assert sorted(result.output.splitlines()) == ["a.py", "b.py"]


@pytest.mark.asyncio
async def test_glob_recursive_pattern(tmp_path: Path) -> None:
    """**/*.py 递归匹配子目录。"""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.py").write_text("d")
    (tmp_path / "top.py").write_text("t")

    tool = Glob(work_dir=tmp_path)
    result = await tool.execute(tool.Params(pattern="**/*.py"))

    assert result.is_error is False
    assert sorted(result.output.splitlines()) == ["sub/deep.py", "top.py"]


@pytest.mark.asyncio
async def test_glob_skips_skip_dirs(tmp_path: Path) -> None:
    """跳过 SKIP_DIRS(如 .git / .venv)。"""
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("v")
    (tmp_path / "main.py").write_text("m")

    tool = Glob(work_dir=tmp_path)
    result = await tool.execute(tool.Params(pattern="**/*.py"))

    assert result.is_error is False
    assert result.output.splitlines() == ["main.py"]


@pytest.mark.asyncio
async def test_glob_no_match(tmp_path: Path) -> None:
    """没有匹配时返回友好提示。"""
    (tmp_path / "a.txt").write_text("a")

    tool = Glob(work_dir=tmp_path)
    result = await tool.execute(tool.Params(pattern="*.py"))

    assert result.is_error is False
    assert "No files matched" in result.output


@pytest.mark.asyncio
async def test_glob_path_not_found(tmp_path: Path) -> None:
    """基础路径不存在时报错。"""
    tool = Glob(work_dir=tmp_path)
    result = await tool.execute(tool.Params(pattern="*.py", path="nonexistent"))

    assert result.is_error is True
    assert "path not found" in result.output


@pytest.mark.asyncio
async def test_glob_relative_path(tmp_path: Path) -> None:
    """path 参数相对 work_dir 解析。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "x.py").write_text("x")

    tool = Glob(work_dir=tmp_path)
    result = await tool.execute(tool.Params(pattern="*.py", path="sub"))

    assert result.is_error is False
    assert result.output.splitlines() == ["x.py"]


def test_glob_is_concurrency_safe() -> None:
    """Glob 是只读工具,可并发。"""
    assert Glob(work_dir=Path("/")).is_concurrency_safe is True


def test_glob_category_is_read() -> None:
    """Glob category 是 read。"""
    assert Glob(work_dir=Path("/")).category == "read"


def test_glob_params_validation() -> None:
    """Params 校验必填 pattern。"""
    tool = Glob(work_dir=Path("/"))
    with pytest.raises(Exception):
        tool.Params()  # type: ignore[call-arg]