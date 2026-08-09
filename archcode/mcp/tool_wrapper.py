"""MCPToolWrapper:把 MCP server 提供的工具包装为 ArchCode 的 Tool 接口。

工具命名规则: mcp_{server_name}_{tool_def.name}(单下划线,避免 MewCode 双下划线 bug)
should_defer=True 让 LLM 通过 ToolSearch 按需加载 schema。
"""

from __future__ import annotations

from typing import Any

from pydantic import create_model

from archcode.tools.base import Tool, ToolResult


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


def _extract_text(content: list[Any]) -> str:
    """从 MCP content 列表里提取文字。Image/EmbeddedResource 显示占位。"""
    parts: list[str] = []
    for block in content:
        if hasattr(block, "text"):
            parts.append(block.text)
        elif hasattr(block, "mimeType"):
            parts.append(f"[image: {block.mimeType}]")
        elif hasattr(block, "resource"):
            resource = block.resource
            if hasattr(resource, "text"):
                parts.append(resource.text)
            elif hasattr(resource, "uri"):
                parts.append(f"[binary resource: {resource.uri}]")
    return "\n".join(parts) if parts else "(no output)"


class MCPToolWrapper(Tool):
    """MCP server 提供的工具包装为 ArchCode Tool 接口。

    属性:
    - name:           mcp_{server_name}_{tool_def.name}
    - should_defer:   True(LLM 通过 ToolSearch 按需加载)
    - category:       "command"(走权限系统的 HITL 弹窗)
    - is_concurrency_safe: False(MCP 调用串行)
    """

    def __init__(self, server_name: str, tool_def: Any, client: Any) -> None:
        self._server_name = server_name
        self._tool_def = tool_def
        self._client = client
        self.name = f"mcp_{server_name}_{tool_def.name}"
        self.description = tool_def.description or tool_def.name
        self.category = "command"
        self.is_concurrency_safe = False
        self.should_defer = True
        # MCP SDK 1.x 用 input_schema(下划线),不是 inputSchema(驼峰)
        schema = getattr(tool_def, "input_schema", None) or getattr(
            tool_def, "inputSchema", None
        )
        self.params_model = _build_params_model(tool_def.name, schema)

    def get_schema(self) -> dict[str, Any]:
        """直接用 MCP server 返回的 input_schema,不重新生成。"""
        schema = getattr(self._tool_def, "input_schema", None) or getattr(
            self._tool_def, "inputSchema", None
        )
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }

    async def execute(self, params: Any) -> ToolResult:
        """调 client.call_tool,返回 ToolResult。

        - client 死了 → 重连一次
        - call_tool 抛异常 → is_error=True + 把 _alive 设 False(下次重连)
        """
        if not self._client.is_alive:
            try:
                await self._client.connect()
            except Exception as e:
                return ToolResult(
                    output=f"MCP server '{self._server_name}' reconnect failed: {e}",
                    is_error=True,
                )

        try:
            result = await self._client.call_tool(
                self._tool_def.name, params.model_dump(exclude_none=True)
            )
        except Exception as e:
            # 标记 client 死亡,下次自动重连
            self._client._alive = False
            return ToolResult(
                output=f"MCP tool call failed: {e}",
                is_error=True,
            )

        text = _extract_text(result.content)
        # MCP SDK 1.x 用 is_error(下划线),不是 isError(驼峰)
        is_err = getattr(result, "is_error", None)
        if is_err is None:
            is_err = getattr(result, "isError", False)
        return ToolResult(output=text, is_error=bool(is_err))