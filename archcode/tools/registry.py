"""ToolRegistry:工具注册中心。

提供工具的注册、启/停控制、延迟加载状态、按协议导出 schema。
"""

from __future__ import annotations

from typing import Any

from archcode.tools.base import Tool


class ToolRegistry:
    """工具注册中心。

    职责:
    - 名字 ↔ Tool 实例的映射(register / get)
    - 启/停控制(enable / disable / enable_all / is_enabled)
    - 延迟加载状态(_discovered 集合 + mark_discovered / is_discovered)
    - 延迟工具查询(get_deferred_tool_names / search_deferred / find_deferred_by_names)
    - 按协议导出 schema(get_all_schemas),自动过滤 should_defer && not discovered
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        self._discovered: set[str] = set()

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

    # ── 延迟加载状态 ──────────────────────────────────────

    def mark_discovered(self, name: str) -> None:
        """标记工具为已发现(完整 schema 已发给 LLM)。"""
        self._discovered.add(name)

    def is_discovered(self, name: str) -> bool:
        return name in self._discovered

    def get_deferred_tool_names(self) -> list[str]:
        """返回所有未发现且未禁用的延迟工具名字列表。"""
        return [
            name
            for name, tool in self._tools.items()
            if getattr(tool, "should_defer", False)
            and name not in self._discovered
            and name not in self._disabled
        ]

    def search_deferred(
        self, query: str, max_results: int, protocol: str = "anthropic"
    ) -> list[dict[str, Any]]:
        """关键词搜索延迟工具,按名字/描述打分排序。"""
        query_lower = query.lower()
        scored: list[tuple[int, str, Tool]] = []
        for name, tool in self._tools.items():
            if not getattr(tool, "should_defer", False):
                continue
            if name in self._disabled:
                continue
            score = 0
            name_lower = name.lower()
            desc_lower = (tool.description or "").lower()
            if query_lower in name_lower:
                score += 10
            if query_lower in desc_lower:
                score += 5
            for word in query_lower.split():
                if word in name_lower:
                    score += 3
                if word in desc_lower:
                    score += 1
            if score > 0:
                scored.append((score, name, tool))
        scored.sort(key=lambda x: x[0], reverse=True)
        results: list[dict[str, Any]] = []
        for _, _name, tool in scored[:max_results]:
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                results.append(base)
        return results

    def find_deferred_by_names(
        self, names: list[str], protocol: str = "anthropic"
    ) -> list[dict[str, Any]]:
        """按精确名字查找延迟工具。"""
        results: list[dict[str, Any]] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            if not getattr(tool, "should_defer", False):
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                results.append(base)
        return results

    # ── 标准输出 ──────────────────────────────────────────

    def list_tools(self) -> list[Tool]:
        """返回所有已注册的工具实例。"""
        return list(self._tools.values())

    def get_all_schemas(self, protocol: str = "anthropic") -> list[dict[str, Any]]:
        """返回所有启用工具的 schema 列表,自动过滤延迟加载。

        - anthropic:返回 [{name, description, input_schema}]
        - openai / openai-compat:返回 [{type: function, ...}]

        延迟工具(should_defer=True)未发现(discovered)时跳过。
        """
        schemas: list[dict[str, Any]] = []
        for name, tool in self._tools.items():
            if name in self._disabled:
                continue
            if getattr(tool, "should_defer", False) and name not in self._discovered:
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                schemas.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:  # anthropic
                schemas.append(base)
        return schemas

    def get_schemas(
        self, allowed: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """返回已注册工具的 schema 列表,支持 allowed 过滤。"""
        schemas: list[dict[str, Any]] = []
        for name, tool in self._tools.items():
            if name in self._disabled:
                continue
            if allowed is not None and name not in allowed:
                continue
            schemas.append(tool.get_schema())
        return schemas