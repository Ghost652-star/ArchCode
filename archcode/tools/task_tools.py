"""TaskList / TaskGet:后台任务状态查询(主 agent 专用,sub-agent-design §8.4/§8.5)。

这两个工具**不在** BACKGROUND_ALLOWED_TOOLS 里——后台 agent 不是任务管理者,
查询方向永远是从主 agent 看向后台任务,反过来不成立(§8.4)。
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel

from archcode.agents.task_manager import BackgroundTask, TaskManager
from archcode.tools.base import Tool, ToolResult

MAX_TASKGET_RESULT_LENGTH = 5000


class _NoParams(BaseModel):
    pass


class TaskGetParams(BaseModel):
    task_id: str


def _elapsed(bg: BackgroundTask) -> str:
    end = bg.end_time if bg.end_time is not None else time.monotonic()
    secs = max(end - bg.start_time, 0.0)
    return f"{secs / 60:.1f}m" if secs >= 60 else f"{secs:.1f}s"


def _format_row(bg: BackgroundTask) -> str:
    return f"{bg.id} | {bg.name} | {bg.status} | {_elapsed(bg)}"


class TaskListTool(Tool):
    name = "TaskList"
    description = (
        "List background sub-agent tasks: ID | Name | Status | Elapsed. "
        "Use TaskGet to fetch a task's result."
    )
    params_model = _NoParams
    category = "read"
    is_concurrency_safe = True

    def __init__(self, task_manager: TaskManager) -> None:
        self._task_manager = task_manager

    async def execute(self, params: BaseModel) -> ToolResult:
        tasks = self._task_manager.list_tasks()
        if not tasks:
            return ToolResult("(no background tasks)")
        header = "ID | Name | Status | Elapsed"
        return ToolResult("\n".join([header, *(_format_row(t) for t in tasks)]))


class TaskGetTool(Tool):
    name = "TaskGet"
    description = (
        "Get one background task's status, progress and final result by task ID. "
        "Use TaskList to discover IDs."
    )
    params_model = TaskGetParams
    category = "read"
    is_concurrency_safe = True

    def __init__(self, task_manager: TaskManager) -> None:
        self._task_manager = task_manager

    async def execute(self, params: BaseModel) -> ToolResult:
        p: TaskGetParams = params  # type: ignore[assignment]
        bg = self._task_manager.get(p.task_id)
        if bg is None:
            return ToolResult(f"Unknown task ID: '{p.task_id}'. Use TaskList.", is_error=True)

        result = bg.result or "(no result yet)"
        if len(result) > MAX_TASKGET_RESULT_LENGTH:
            result = result[:MAX_TASKGET_RESULT_LENGTH] + "\n... (truncated)"
        return ToolResult(
            f"Task ID: {bg.id}\n"
            f"Agent: {bg.name}\n"
            f"Status: {bg.status}\n"
            f"Elapsed: {_elapsed(bg)}\n"
            f"Progress: tool_calls={bg.progress.tool_call_count}, "
            f"input_tokens={bg.progress.input_tokens}, output_tokens={bg.progress.output_tokens}\n"
            f"Result:\n{result}"
        )


def register_task_tools(registry: Any, task_manager: TaskManager) -> None:
    """把两个查询工具注册进主注册表(T10 接线用)。"""
    registry.register(TaskListTool(task_manager))
    registry.register(TaskGetTool(task_manager))
