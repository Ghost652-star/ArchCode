"""Tool 基类、ToolResult、常量定义。

对齐 mewcode/tools/base.py,去掉 should_defer 字段(v0.2 不做 defer 机制)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".tox", ".mypy_cache"}

MAX_OUTPUT_CHARS = 10000

ToolCategory = Literal["read", "write", "command"]


@dataclass
class ToolResult:
    """工具执行结果。所有工具的 execute() 必须返回这个类型。

    不抛异常:失败也返回 is_error=True 的 result,LLM 下一轮能看到。
    """

    output: str
    is_error: bool = False


class Tool(ABC):
    """工具抽象基类。

    子类必须:
    1. 设置 name / description / params_model 三个类属性
    2. 重写 async execute(self, params) 方法

    可选类属性:
    - category:工具分类,默认 "read"(安全默认值)
    - is_concurrency_safe:能否并发执行,默认 False
    - is_system_tool:是否系统级基础设施,默认 False(v0.2 不消费,留给 v0.3+)
    """

    name: str
    description: str
    params_model: type[BaseModel]
    category: ToolCategory = "read"
    is_concurrency_safe: bool = False
    is_system_tool: bool = False  # v0.2 保留,框架暂不消费

    @property
    def is_read_only(self) -> bool:
        """便捷属性:category == "read" 时为 True。"""
        return self.category == "read"

    def get_schema(self) -> dict[str, Any]:
        """生成 Anthropic 风格的工具 schema。

        返回:{name, description, input_schema}
        - input_schema 来自 Pydantic 的 model_json_schema(),去掉 title
        - OpenAI 协议再在外面包一层 {type: function, ...}
        """
        schema = self.params_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }

    @abstractmethod
    async def execute(self, params: BaseModel) -> ToolResult:
        """执行工具的入口。子类必须实现。"""
        ...