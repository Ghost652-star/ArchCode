"""Tool 基类测试。"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from archcode.tools.base import (
    MAX_OUTPUT_CHARS,
    SKIP_DIRS,
    Tool,
    ToolCategory,
    ToolResult,
)


class DummyParams(BaseModel):
    path: str = Field(description="Path to read")


class DummyTool(Tool):
    name = "DummyTool"
    description = "A dummy tool for testing."
    params_model = DummyParams
    category = "read"
    is_concurrency_safe = True

    async def execute(self, params: DummyParams) -> ToolResult:
        return ToolResult(output=f"executed: {params.path}")


class WriteTool(Tool):
    name = "WriteTool"
    description = "Writes something."
    params_model = DummyParams
    category = "write"

    async def execute(self, params: DummyParams) -> ToolResult:
        return ToolResult(output=f"written: {params.path}")


def test_tool_result_default_is_error_false() -> None:
    """ToolResult 默认 is_error=False。"""
    r = ToolResult(output="ok")
    assert r.output == "ok"
    assert r.is_error is False


def test_tool_result_can_be_error() -> None:
    """ToolResult 可以标记错误。"""
    r = ToolResult(output="bad", is_error=True)
    assert r.is_error is True


def test_tool_category_literal_values() -> None:
    """category 必须是 read/write/command。"""
    assert DummyTool().category == "read"
    assert WriteTool().category == "write"


def test_tool_category_default_is_read() -> None:
    """子类忘了声明 category 时,默认是 read(安全默认值)。"""
    class NoCategory(Tool):
        name = "NoCategory"
        description = "x"
        params_model = DummyParams

        async def execute(self, params: DummyParams) -> ToolResult:
            return ToolResult(output="x")

    assert NoCategory().category == "read"


def test_is_read_only_property() -> None:
    """is_read_only 等价于 category == 'read'。"""
    assert DummyTool().is_read_only is True
    assert WriteTool().is_read_only is False


def test_is_concurrency_safe_default_false() -> None:
    """is_concurrency_safe 默认 False。"""
    assert WriteTool().is_concurrency_safe is False


def test_get_schema_anthropic_format() -> None:
    """get_schema() 返回 Anthropic 风格 dict(含 input_schema,无 title)。"""
    schema = DummyTool().get_schema()
    assert schema["name"] == "DummyTool"
    assert schema["description"] == "A dummy tool for testing."
    assert "input_schema" in schema
    assert "title" not in schema["input_schema"]
    assert schema["input_schema"]["properties"]["path"]["description"] == "Path to read"


def test_get_schema_includes_field_description() -> None:
    """Field(description=...) 进 JSON Schema 的 properties.*.description。"""
    schema = DummyTool().get_schema()
    assert schema["input_schema"]["properties"]["path"]["type"] == "string"


def test_skip_dirs_contains_common_build_dirs() -> None:
    """SKIP_DIRS 包含 .git / .venv / node_modules 等。"""
    assert ".git" in SKIP_DIRS
    assert ".venv" in SKIP_DIRS
    assert "node_modules" in SKIP_DIRS
    assert "__pycache__" in SKIP_DIRS


def test_max_output_chars_is_10000() -> None:
    """MAX_OUTPUT_CHARS 常量是 10000。"""
    assert MAX_OUTPUT_CHARS == 10000


def test_tool_is_abstract() -> None:
    """Tool 是抽象类,不能直接实例化。"""
    with pytest.raises(TypeError):
        Tool()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_tool_execute_returns_tool_result() -> None:
    """execute() 返回 ToolResult。"""
    result = await DummyTool().execute(DummyParams(path="x"))
    assert isinstance(result, ToolResult)
    assert result.output == "executed: x"
    assert result.is_error is False