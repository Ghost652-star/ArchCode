"""AskUserQuestion 工具 —— LLM 向用户提问的交互工具。

LLM 在 plan mode（或其他需要确认的场景）下，向用户提出多项选择题，
等待用户选择后，将结果返回给 LLM。

category 为 "interactive"，不走普通 read/write/command 判定，
plan mode 下直接放行。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from archcode.tools.base import Tool, ToolResult


class AskUserQuestion(Tool):
    """向用户提问多项选择题。"""

    name = "AskUserQuestion"
    description = "向用户提出多项选择题，等待用户选择后返回。"

    category = "interactive"

    class Params(BaseModel):
        question: str
        header: str | None = None
        multi_select: bool = False
        options: list[dict[str, str]]

    params_model = Params

    async def execute(self, params: Params) -> ToolResult:
        # 实际提问逻辑由 agent.py 通过 PermissionRequest 事件
        # 交给 app.py 的 PermissionModal 处理。
        # 这里不应该被执行——checker.check() 会返回 ask，触发 HITL。
        return ToolResult(
            output=(
                f"[AskUserQuestion 执行了 execute() — 这是异常路径"
                "，正常应该走 HITL 弹窗。question={params.question!r}"
            ),
            is_error=True,
        )
