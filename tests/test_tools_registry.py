"""ToolRegistry 测试。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from archcode.tools.base import Tool, ToolResult
from archcode.tools.registry import ToolRegistry


class Params(BaseModel):
    path: str = Field(description="Path")


class T1(Tool):
    name = "T1"
    description = "first tool"
    params_model = Params
    category = "read"
    is_concurrency_safe = True

    async def execute(self, params: Params) -> ToolResult:
        return ToolResult(output="t1")


class T2(Tool):
    name = "T2"
    description = "second tool"
    params_model = Params
    category = "write"

    async def execute(self, params: Params) -> ToolResult:
        return ToolResult(output="t2")


def test_register_and_get() -> None:
    """register / get 双向工作。"""
    reg = ToolRegistry()
    reg.register(T1())
    assert reg.get("T1") is not None
    assert reg.get("T1").name == "T1"
    assert reg.get("Unknown") is None


def test_is_enabled_default_true() -> None:
    """注册后默认启用。"""
    reg = ToolRegistry()
    reg.register(T1())
    assert reg.is_enabled("T1") is True


def test_is_enabled_false_for_unknown() -> None:
    """未注册的工具返回 False。"""
    reg = ToolRegistry()
    assert reg.is_enabled("Unknown") is False


def test_disable_then_enable() -> None:
    """disable 后 is_enabled=False,enable 后恢复。"""
    reg = ToolRegistry()
    reg.register(T1())
    reg.disable("T1")
    assert reg.is_enabled("T1") is False
    reg.enable("T1")
    assert reg.is_enabled("T1") is True


def test_disable_unknown_does_not_raise() -> None:
    """disable 未注册的工具不报错(静默)。"""
    reg = ToolRegistry()
    reg.disable("Unknown")  # 不应抛异常
    assert reg.is_enabled("Unknown") is False


def test_enable_unknown_does_not_raise() -> None:
    """enable 未注册的工具不报错。"""
    reg = ToolRegistry()
    reg.enable("Unknown")
    assert reg.is_enabled("Unknown") is False


def test_enable_all() -> None:
    """enable_all 一次性启用所有。"""
    reg = ToolRegistry()
    reg.register(T1())
    reg.register(T2())
    reg.disable("T1")
    reg.disable("T2")
    assert reg.is_enabled("T1") is False
    assert reg.is_enabled("T2") is False
    reg.enable_all()
    assert reg.is_enabled("T1") is True
    assert reg.is_enabled("T2") is True


def test_list_tools_returns_all_registered() -> None:
    """list_tools 返回所有注册的工具。"""
    reg = ToolRegistry()
    reg.register(T1())
    reg.register(T2())
    names = {t.name for t in reg.list_tools()}
    assert names == {"T1", "T2"}


def test_get_all_schemas_anthropic_protocol() -> None:
    """anthropic 协议返回 [{name, description, input_schema}]。"""
    reg = ToolRegistry()
    reg.register(T1())
    schemas = reg.get_all_schemas(protocol="anthropic")
    assert len(schemas) == 1
    s = schemas[0]
    assert s["name"] == "T1"
    assert s["description"] == "first tool"
    assert "input_schema" in s
    assert "type" not in s  # anthropic 格式不带 type


def test_get_all_schemas_openai_protocol() -> None:
    """openai 协议返回 [{type: function, name, description, parameters}]。"""
    reg = ToolRegistry()
    reg.register(T1())
    schemas = reg.get_all_schemas(protocol="openai")
    assert len(schemas) == 1
    s = schemas[0]
    assert s["type"] == "function"
    assert s["name"] == "T1"
    assert s["description"] == "first tool"
    assert "parameters" in s


def test_get_all_schemas_openai_compat_protocol() -> None:
    """openai-compat 也走 OpenAI 格式(由 client 层再做嵌套转换)。"""
    reg = ToolRegistry()
    reg.register(T1())
    schemas = reg.get_all_schemas(protocol="openai-compat")
    assert schemas[0]["type"] == "function"
    assert "parameters" in schemas[0]


def test_get_all_schemas_skips_disabled() -> None:
    """disabled 工具不出现在 schemas 里。"""
    reg = ToolRegistry()
    reg.register(T1())
    reg.register(T2())
    reg.disable("T2")
    schemas = reg.get_all_schemas(protocol="anthropic")
    names = {s["name"] for s in schemas}
    assert names == {"T1"}


def test_get_all_schemas_empty_registry() -> None:
    """空 registry 返回空 list。"""
    reg = ToolRegistry()
    assert reg.get_all_schemas(protocol="anthropic") == []


def test_get_all_schemas_default_protocol_is_anthropic() -> None:
    """get_all_schemas 默认 protocol 是 anthropic。"""
    reg = ToolRegistry()
    reg.register(T1())
    schemas = reg.get_all_schemas()
    assert "input_schema" in schemas[0]