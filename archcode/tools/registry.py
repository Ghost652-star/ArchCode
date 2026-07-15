"""ToolRegistry:工具注册中心。

对齐 mewcode/tools/__init__.py 的 ToolRegistry 类,去掉 defer 相关方法和字段:
- 删除:_discovered / mark_discovered / is_discovered / get_deferred_tool_names /
  search_deferred / find_deferred_by_names
- get_all_schemas 不再过滤 should_defer 工具
"""

from __future__ import annotations

from typing import Any

from archcode.tools.base import Tool


class ToolRegistry:
    """工具注册中心。

    职责:
    - 名字 ↔ Tool 实例的映射(register / get)
    - 启/停控制(enable / disable / enable_all / is_enabled)
    - 列出所有工具(list_tools)
    - 把工具列表转成各协议 schema 格式(get_all_schemas)
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()

    def register(self, tool: Tool) -> None:
        """注册一个 Tool 实例。同名工具会被覆盖。"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """按名字取 Tool 实例。未注册返回 None。"""
        return self._tools.get(name)

    def is_enabled(self, name: str) -> bool:
        """工具是否启用。必须已注册且未被 disable。"""
        return name in self._tools and name not in self._disabled

    def enable(self, name: str) -> None:
        """启用工具。重复 enable 是幂等的。"""
        self._disabled.discard(name)

    def disable(self, name: str) -> None:
        """禁用工具。未注册的工具静默忽略。"""
        if name in self._tools:
            self._disabled.add(name)

    def enable_all(self) -> None:
        """启用所有工具。"""
        self._disabled.clear()

    def list_tools(self) -> list[Tool]:
        """返回所有已注册的工具实例。"""
        return list(self._tools.values())

    def get_all_schemas(self, protocol: str = "anthropic") -> list[dict[str, Any]]:
        """返回所有启用工具的 schema 列表,按协议分发。

        - anthropic:返回 [{name, description, input_schema}]
        - openai / openai-compat:返回 [{type: function, name, description, parameters}]
          (OpenAICompatClient 内部会再包一层 function 嵌套,转 Chat Completions 格式)
        """
        schemas: list[dict[str, Any]] = []
        for name, tool in self._tools.items():
            if name in self._disabled:
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                schemas.append(
                    {
                        "type": "function",
                        "name": base["name"],
                        "description": base["description"],
                        "parameters": base["input_schema"],
                    }
                )
            else:  # anthropic
                schemas.append(base)
        return schemas