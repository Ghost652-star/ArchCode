"""Worktree 隔离的数据模型(worktree-design §2.7)。

两个 dataclass:
- Worktree:一个已创建 worktree 实例的句柄(active 表的值);
- WorktreeSession:进入某 worktree 时的状态快照(退出/恢复时据此回家)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


class WorktreeError(Exception):
    """worktree 管理错误(create/enter/exit 的失败出口,错误文本直接返回 LLM)。"""


@dataclass
class Worktree:
    """一个已创建的 worktree(§2.7)。head_commit 双用:收尾判定基线 + 恢复数据源。"""

    name: str
    path: str
    branch: str
    based_on: str
    head_commit: str
    created: datetime = field(default_factory=datetime.now)


@dataclass
class WorktreeSession:
    """进入某 worktree 时的快照(§2.7)——退出时恢复回去,崩溃恢复的基础。

    hook_based:由 Hook 路径创建的 worktree 走不同收尾策略(预留标记)。
    """

    original_cwd: str
    worktree_path: str
    worktree_name: str
    original_branch: str
    original_head_commit: str
    session_id: str = ""
    hook_based: bool = False
