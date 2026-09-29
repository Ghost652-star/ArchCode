"""TodoWrite:会话级任务清单维护(多步任务的进度跟踪,Web UI 面板可视化)。

设计要点:
- 全量替换语义:LLM 每次传完整清单,避免增量同步的复杂性;
- 只改会话内 UI 状态,不触文件系统 → category="read" 不走写权限;
- store 挂在工具实例上,webui 经 registry 只读取快照渲染面板;
  会话切换时由 webui 负责清空(TODO 清单是会话作用域)。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from archcode.tools.base import Tool, ToolResult

MAX_TODOS = 50
_VALID_STATUS = ("pending", "in_progress", "completed")


class TodoItemParams(BaseModel):
    content: str = Field(description="The task description")
    status: str = Field(description="One of: pending, in_progress, completed")


class TodoWriteParams(BaseModel):
    todos: list[TodoItemParams] = Field(
        description="The FULL replacement todo list, in display order. Empty list clears it."
    )


class TodoStore:
    """当前会话的任务清单(内存态)。"""

    def __init__(self) -> None:
        self.todos: list[dict] = []

    def replace(self, items: list[dict]) -> None:
        self.todos = items

    def clear(self) -> None:
        self.todos = []


class TodoWriteTool(Tool):
    name = "TodoWrite"
    description = (
        "Write the session todo list for multi-step tasks. Pass the FULL list every "
        "time (it replaces the previous one). Keep exactly one item in_progress and "
        "mark items completed as soon as they are done. Skip it for trivial tasks."
    )
    params_model = TodoWriteParams
    category = "read"
    is_concurrency_safe = True

    def __init__(self) -> None:
        self.store = TodoStore()

    async def execute(self, params: TodoWriteParams) -> ToolResult:
        if len(params.todos) > MAX_TODOS:
            return ToolResult(output=f"Error: too many todos (max {MAX_TODOS})", is_error=True)
        items = [
            {"content": t.content, "status": t.status if t.status in _VALID_STATUS else "pending"}
            for t in params.todos
        ]
        self.store.replace(items)
        if not items:
            return ToolResult(output="(todo list cleared)")
        done = sum(1 for i in items if i["status"] == "completed")
        active = next((i["content"] for i in items if i["status"] == "in_progress"), "-")
        lines = [f"[{done}/{len(items)}] in_progress: {active}"]
        lines.extend(f"{i['status']:>12} | {i['content']}" for i in items)
        return ToolResult(output="\n".join(lines))
