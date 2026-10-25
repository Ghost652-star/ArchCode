"""后台任务管理(sub-agent-design §8.2)。

一个后台任务 = 一个 BackgroundTask 实例,统一由 TaskManager 管理——类比
操作系统的进程表/任务队列:launch 登记 → 运行 → 完成/失败 → 通知回收。
通知经内部队列交主循环 drain 后注入 <task-notification>(§9.2)。

异常保护(§8.2):子 agent 崩溃只把 status 置 failed,不影响主程序;
通知在 finally 里发——无论成败,主 agent 永远知道结局。
cancel 字段/方法供延后的 ESC 手动切换与 adoptRunning 使用(§8.1/8.3)。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from archcode.logctx import task_scope

log = logging.getLogger(__name__)


@dataclass
class ProgressInfo:
    """任务进度(§8.2:工具调用次数、token 消耗、最近活动)——TaskGet 的数据源。"""

    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    last_activity: str = ""


def describe_tool_call(tool_name: str, args: dict[str, Any] | None) -> str:
    """工具调用的人读一句话摘要(任务面板"此刻在干嘛"/TaskGet 用)。"""
    args = args or {}

    def _short(value: str, limit: int = 48) -> str:
        return value if len(value) <= limit else value[: limit - 1] + "…"

    path = str(args.get("file_path") or args.get("path") or "").strip()
    if tool_name in ("ReadFile", "WriteFile", "EditFile") and path:
        verb = {"ReadFile": "读取", "WriteFile": "写入", "EditFile": "编辑"}[tool_name]
        from pathlib import Path as _P

        return f"{verb} {_short(_P(path).name or path)}"
    if tool_name == "Bash":
        return f"运行 {_short(str(args.get('command', '')).strip() or '(空命令)')}"
    if tool_name in ("Grep", "Glob"):
        return f"搜索 {args.get('pattern', '')}"
    if tool_name == "Agent":
        return f"派发 {args.get('description') or args.get('subagent_type') or '子任务'}"
    if tool_name == "SendMessage":
        return f"发消息给 {args.get('to', '')}"
    if tool_name in ("TaskCreate", "TaskUpdate", "TaskGet", "TaskList"):
        return f"{tool_name} {str(args.get('task_id') or args.get('subject') or '')}".strip()
    return tool_name


class ProgressRecorder:
    """后台任务(队员/子 agent)运行中的实时进度记录器。

    挂在子 Agent 的 `_progress_recorder` 上:agent._execute_tool 入口喂工具
    调用,用量在每轮 UsageEvent 处累计;launch 后把 `BackgroundTask.progress`
    重绑到 recorder.info(同一对象双引用),/api/tasks 与 TaskGet 读它即实时。
    单事件循环内更新,不加锁;/api/tasks 是 sync endpoint(线程池读),CPython
    下 int/str 赋值原子,最坏读到上一次的值,可接受。
    """

    def __init__(self) -> None:
        self.info = ProgressInfo()

    def bind_task(self, bg: "BackgroundTask") -> None:
        """把 BackgroundTask.progress 换成 recorder 持有的实例(保留已有累计)。"""
        self.info.tool_call_count = bg.progress.tool_call_count or self.info.tool_call_count
        self.info.input_tokens = bg.progress.input_tokens or self.info.input_tokens
        self.info.output_tokens = bg.progress.output_tokens or self.info.output_tokens
        self.info.last_activity = self.info.last_activity or bg.progress.last_activity
        bg.progress = self.info

    def record_tool(self, tool_name: str, args: dict[str, Any] | None) -> None:
        self.info.tool_call_count += 1
        self.info.last_activity = describe_tool_call(tool_name, args)

    def record_usage(self, input_tokens: int, output_tokens: int) -> None:
        """累计制:agent 每轮把自身累计总量传入(非增量)。"""
        self.info.input_tokens = input_tokens
        self.info.output_tokens = output_tokens


@dataclass
class BackgroundTask:
    """一个后台任务的完整生命周期记录(§8.2 结构图)。"""

    id: str
    name: str
    agent: Any  # 提供 async run_to_completion(task: str) -> str(T7 的子 agent 包装)
    task: str
    status: str = "running"  # running / completed / failed / cancelled
    result: str = ""
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    cancel: Callable[[], None] | None = None  # 延后的 ESC / 超时转换使用(§8.1/8.3)
    progress: ProgressInfo = field(default_factory=ProgressInfo)


class TaskManager:
    """全部后台任务的生命周期管理(§8.2)。launch 是核心入口。"""

    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._notify_queue: asyncio.Queue[str] = asyncio.Queue()
        self._async_tasks: dict[str, asyncio.Task[None]] = {}

    def launch(self, agent: Any, task: str, name: str = "") -> str:
        """登记并以后台协程启动一个任务;立即返回 task_id(§8.2)。

        `agent` 需提供 `async run_to_completion(task: str) -> str`
        (T7 的子 agent 包装对象;测试用 Fake)。
        """
        task_id = uuid.uuid4().hex[:8]
        bg = BackgroundTask(id=task_id, name=name or task_id, agent=agent, task=task)
        self._tasks[task_id] = bg
        log.info("bg task launched: id=%s name=%s", task_id, bg.name)
        async_task = asyncio.create_task(self._run_background(task_id))
        self._async_tasks[task_id] = async_task
        bg.cancel = async_task.cancel
        return task_id

    async def _run_background(self, task_id: str) -> None:
        bg = self._tasks.get(task_id)
        if bg is None:
            return
        with task_scope(task_id):  # 关联列:本任务协程内的日志行都带 task id
            try:
                bg.result = await bg.agent.run_to_completion(bg.task)
                bg.status = "completed"
            except asyncio.CancelledError:
                bg.status = "cancelled"
                bg.result = "Task was cancelled"
            except Exception as exc:  # 异常保护:子 agent 崩溃不影响主程序(§8.2)
                bg.status = "failed"
                bg.result = f"Error: {exc}"
            finally:
                bg.end_time = time.monotonic()
                # 有 recorder 的任务 progress 已实时累计(不覆盖);无 recorder 的
                # 结束时一次性快照(runner 暴露 total_* 才有值)
                if not bg.progress.input_tokens and not bg.progress.output_tokens:
                    bg.progress.input_tokens = int(getattr(bg.agent, "total_input_tokens", 0) or 0)
                    bg.progress.output_tokens = int(getattr(bg.agent, "total_output_tokens", 0) or 0)
                self._async_tasks.pop(task_id, None)
                if bg.status == "completed":
                    log.info(
                        "bg task completed: id=%s name=%s elapsed=%.1fs tokens(in/out)=%d/%d",
                        task_id,
                        bg.name,
                        bg.end_time - bg.start_time,
                        bg.progress.input_tokens,
                        bg.progress.output_tokens,
                    )
                elif bg.status == "cancelled":
                    log.warning("bg task cancelled: id=%s name=%s", task_id, bg.name)
                else:
                    log.error(
                        "bg task failed: id=%s name=%s error=%.200s",
                        task_id, bg.name, bg.result,
                    )
                await self._notify_queue.put(task_id)  # 完成即通知,无论成败

    def drain_notifications(self) -> list[BackgroundTask]:
        """主循环每轮调用:取走全部已完成任务(§9.2 注入 <task-notification>)。"""
        completed: list[BackgroundTask] = []
        while not self._notify_queue.empty():
            try:
                task_id = self._notify_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            bg = self._tasks.get(task_id)
            if bg is not None:
                completed.append(bg)
        return completed

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[BackgroundTask]:
        return list(self._tasks.values())

    def cancel(self, task_id: str) -> bool:
        """取消一个运行中的任务(字段与方法的插座给延后的 ESC/超时转换,§8.1)。"""
        bg = self._tasks.get(task_id)
        if bg is None or bg.status != "running":
            return False
        async_task = self._async_tasks.get(task_id)
        if async_task is not None and not async_task.done():
            log.info("bg task cancel requested: id=%s", task_id)
            async_task.cancel()
            return True
        return False


class SubAgentRunner:
    '''run_to_completion 适配(AgentTool 与 skill fork 共用,§3.2/§13)。

    TaskManager 只依赖 `async run_to_completion(task) -> str`。
    inject_task=True(定义式):task 作为 user 消息注入空白对话(§3.4);
    inject_task=False(Fork / skill fork):任务已在对话末尾,传空串即可
    (serializer 合并相邻 user 消息,无空消息问题)。
    '''

    def __init__(self, agent: Any, conversation: Any, inject_task: bool) -> None:
        self.agent = agent
        self._conversation = conversation
        self._inject_task = inject_task
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    async def run_to_completion(self, task: str) -> str:
        if self._inject_task:
            self._conversation.add_user(task)
        result = await self.agent.run_to_completion("", self._conversation)
        self.total_input_tokens = int(getattr(self.agent, "total_input_tokens", 0) or 0)
        self.total_output_tokens = int(getattr(self.agent, "total_output_tokens", 0) or 0)
        return result
