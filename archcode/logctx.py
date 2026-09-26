"""日志关联上下文:session / task 两个关联 ID 随 async 任务链传播。

三条语义(实施计划 logging-implementation-plan §组件与接口):

1. asyncio.create_task 拷贝当前 context → 后台任务自动继承父会话;
   task id 由 TaskManager._run_background 的 task_scope 注入,
   子 agent 在任务内的所有日志行都带这一列。
2. 只在"有值"时 set(非 None):子 agent 的对话不绑 Session,
   Agent.run() 入口重绑不得清掉继承来的会话值。
3. CorrelationFilter 挂在 handler 上——emit 时取值(动态),
   而不是 logger attach 时(静态)。
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from typing import Iterator

_SESSION: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "archcode_log_session", default=None
)
_TASK: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "archcode_log_task", default=None
)


def get_session_id() -> str | None:
    return _SESSION.get()


def get_task_id() -> str | None:
    return _TASK.get()


def set_session_id(value: str | None) -> contextvars.Token:
    return _SESSION.set(value)


def set_task_id(value: str | None) -> contextvars.Token:
    return _TASK.set(value)


@contextmanager
def task_scope(task_id: str) -> Iterator[None]:
    """TaskManager._run_background 用:任务协程内绑定 task 关联列,退出自动还原。"""
    token = _TASK.set(task_id)
    try:
        yield
    finally:
        _TASK.reset(token)


class CorrelationFilter(logging.Filter):
    """handler 级 Filter:每条 record 落地时注入关联列(缺省 '-')。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.sess = _SESSION.get() or "-"  # type: ignore[attr-defined]
        record.task = _TASK.get() or "-"  # type: ignore[attr-defined]
        return True
