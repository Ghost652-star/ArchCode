"""MCPToolWrapper:把 MCP server 提供的工具包装为 ArchCode 的 Tool 接口。

工具命名规则: mcp_{server_name}_{tool_def.name}(单下划线,避免 MewCode 双下划线 bug)
should_defer=True 让 LLM 通过 ToolSearch 按需加载 schema。
"""

from __future__ import annotations

from typing import Any

from pydantic import create_model


def _json_type_to_python(json_type: str) -> type:
    """JSON Schema 顶层类型 → Python 类型映射。未知类型 fallback 到 str。"""
    mapping: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    return mapping.get(json_type, str)


def _build_params_model(tool_name: str, input_schema: dict[str, Any]):
    """把 MCP inputSchema 转成 Pydantic BaseModel。

    支持顶层标量类型(string/integer/number/boolean/object/array)。
    不支持 $ref / anyOf / enum / default / description。

    required → 必填字段(type, ...);
    其他 → 可选字段(type | None, None),默认 None。
    """
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    field_definitions: dict[str, Any] = {}
    for name, prop in properties.items():
        py_type = _json_type_to_python(prop.get("type", "string"))
        if name in required:
            field_definitions[name] = (py_type, ...)
        else:
            field_definitions[name] = (py_type | None, None)

    return create_model(f"{tool_name}Params", **field_definitions)