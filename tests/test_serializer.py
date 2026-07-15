"""测试 build_anthropic_tools 和 build_openai_tools 函数。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from archcode.llm.serializer import build_anthropic_tools, build_openai_tools
from archcode.tools.base import Tool, ToolResult


class Params(BaseModel):
    path: str = Field(description="path to something")
    count: int = Field(default=1, description="how many")


class T(Tool):
    name = "T"
    description = "test tool"
    params_model = Params
    category = "read"

    async def execute(self, params: Params) -> ToolResult:
        return ToolResult(output="ok")


def test_build_anthropic_tools_returns_list_of_dicts() -> None:
    """build_anthropic_tools 返回 [{name, description, input_schema}] 列表。"""
    schemas = build_anthropic_tools([T()])
    assert len(schemas) == 1
    s = schemas[0]
    assert s["name"] == "T"
    assert s["description"] == "test tool"
    assert "input_schema" in s
    assert "type" not in s


def test_build_anthropic_tools_includes_field_descriptions() -> None:
    """input_schema 包含 Pydantic 字段描述。"""
    schemas = build_anthropic_tools([T()])
    props = schemas[0]["input_schema"]["properties"]
    assert props["path"]["description"] == "path to something"
    assert props["count"]["description"] == "how many"


def test_build_anthropic_tools_empty_list() -> None:
    """空列表返回空列表。"""
    assert build_anthropic_tools([]) == []


def test_build_openai_tools_returns_function_format() -> None:
    """build_openai_tools 返回 [{type: function, name, description, parameters}] 列表。"""
    schemas = build_openai_tools([T()])
    assert len(schemas) == 1
    s = schemas[0]
    assert s["type"] == "function"
    assert s["name"] == "T"
    assert s["description"] == "test tool"
    assert "parameters" in s


def test_build_openai_tools_parameters_has_fields() -> None:
    """parameters 包含 Pydantic 字段定义。"""
    schemas = build_openai_tools([T()])
    props = schemas[0]["parameters"]["properties"]
    assert "path" in props
    assert "count" in props


def test_build_openai_tools_empty_list() -> None:
    """空列表返回空列表。"""
    assert build_openai_tools([]) == []


def test_build_anthropic_strips_title() -> None:
    """input_schema 不带 title(跟 Tool.get_schema 一致)。"""
    schemas = build_anthropic_tools([T()])
    assert "title" not in schemas[0]["input_schema"]


def test_build_openai_strips_title() -> None:
    """parameters 不带 title。"""
    schemas = build_openai_tools([T()])
    assert "title" not in schemas[0]["parameters"]