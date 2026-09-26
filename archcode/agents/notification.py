"""<task-notification> 格式化与注入(sub-agent-design §9.1/9.2)。

后台任务完成(无论成败)→ TaskManager 通知队列 → 主循环每轮 drain →
以 **user 消息身份**注入对话——不对应任何未完成的 tool_use,不破坏
tool-pair 配对;注入发生在两轮 LLM 调用之间的固定点,不打断当前工作。
它长得像 user 消息但不是用户说的话——靠 <task-notification> 标签区分。
"""

from __future__ import annotations

import logging
from typing import Callable

from archcode.agents.task_manager import BackgroundTask, TaskManager
from archcode.conversation.manager import ConversationManager

log = logging.getLogger(__name__)

MAX_NOTIFICATION_RESULT_LENGTH = 5000


def format_task_notification(task: BackgroundTask) -> str:
    """§9.1 结构:Task ID / Agent / Status / Elapsed / Tokens / Result(截断)。"""
    result = task.result or ""
    if len(result) > MAX_NOTIFICATION_RESULT_LENGTH:
        result = result[:MAX_NOTIFICATION_RESULT_LENGTH] + "\n... (truncated)"

    elapsed = ""
    if task.end_time is not None:
        secs = max(task.end_time - task.start_time, 0.0)
        elapsed = f"{secs / 60:.1f}m" if secs >= 60 else f"{secs:.1f}s"

    tokens = ""
    if task.progress.input_tokens or task.progress.output_tokens:
        tokens = (
            f"Tokens: input={task.progress.input_tokens}, "
            f"output={task.progress.output_tokens}\n"
        )

    return (
        f"<task-notification>\n"
        f"Task ID: {task.id}\n"
        f"Agent: {task.name}\n"
        f"Status: {task.status}\n"
        f"Elapsed: {elapsed}\n"
        f"{tokens}Result:\n{result}\n"
        f"</task-notification>"
    )


def make_background_notifier(
    task_manager: TaskManager,
) -> Callable[[ConversationManager], None]:
    """接线用(§11/§10):返回挂到 `Agent._background_notifier` 的回调。

    回调在主循环每轮的固定点被调:drain 通知队列 → 逐条格式化 →
    add_user 注入对话(§9.2)。
    """

    def _drain(conversation: ConversationManager) -> None:
        drained = task_manager.drain_notifications()
        for task in drained:
            conversation.add_user(format_task_notification(task))
        if drained:
            log.debug("bg notifications drained: %d", len(drained))

    return _drain
