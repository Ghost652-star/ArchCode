"""ToolSearchTool:LLM 按需加载延迟工具的 schema。

两种查询:
- select:tool1,tool2 → 精确按名字查找
- 关键词 → 模糊搜索(name + description 打分)

找到后调 registry.mark_discovered,下一轮 schema 才会包含这些工具。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from archcode.tools.base import Tool, ToolResult


class ToolSearchParams(BaseModel):
    query: str = Field(description="select:<name>[,<name>...] 或关键词")
    max_results: int = Field(default=5, description="关键词搜索时返回的最大数")


class ToolSearchTool(Tool):
    """延迟工具的搜索入口。LLM 用来加载 MCP 工具的完整 schema。"""

    name = "ToolSearch"
    description = (
        "搜索并加载延迟工具的完整 schema。"
        "用法:query='select:tool_name' 精确查找;"
        "或直接给关键词模糊搜索。"
    )

    class Params(ToolSearchParams):
        pass

    params_model = Params
    category = "read"
    should_defer = False  # 自身始终可见
    is_concurrency_safe = True

    def __init__(self, registry: Any, protocol: str = "anthropic") -> None:
        super().__init__()
        self._registry = registry
        self._protocol = protocol

    async def execute(self, params: BaseModel) -> ToolResult:
        assert isinstance(params, ToolSearchParams)
        query = params.query
        max_results = params.max_results

        if query.startswith("select:"):
            names = [n.strip() for n in query[len("select:"):].split(",") if n.strip()]
            schemas = self._registry.find_deferred_by_names(names, self._protocol)
        else:
            schemas = self._registry.search_deferred(
                query, max_results, self._protocol
            )

        if not schemas:
            deferred_names = self._registry.get_deferred_tool_names()
            return ToolResult(
                output=(
                    f'No matching deferred tools for "{query}".\n'
                    f"Available: {', '.join(deferred_names)}"
                )
            )

        for s in schemas:
            if "name" in s:
                self._registry.mark_discovered(s["name"])

        return ToolResult(
            output=(
                f"Found {len(schemas)} tool(s). Their full schemas are now loaded:\n\n"
                f"{json.dumps(schemas, indent=2, ensure_ascii=False)}"
            )
        )