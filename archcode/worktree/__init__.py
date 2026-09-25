"""Git Worktree 隔离的接口 stub(sub-agent-design §7.2:本批只落接口)。

方法签名即未来契约:创建 / 收尾 / 清理。当前一律抛 NotImplementedError——
调用方(AgentTool 的 isolation 检查)应在进入这里之前显式报错,
绝不静默假装隔离(§7.2 拍板:报错,不降级)。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class WorktreeHandle:
    """一个已创建 worktree 的句柄(路径 + 分支),供收尾策略使用。"""

    path: Path
    branch: str


class WorktreeManager:
    """接口 stub。真实现(随 worktree 模块落地):git worktree add / 收尾 / 清理。"""

    def __init__(self, work_dir: str | Path) -> None:
        self._work_dir = Path(work_dir)

    def create(self, name: str, ref: str = "HEAD") -> WorktreeHandle:
        raise NotImplementedError("worktree 隔离尚未实现(sub-agent-design §7.2)")

    def auto_cleanup(self, name: str, head_commit: str | None = None) -> None:
        raise NotImplementedError("worktree 隔离尚未实现(sub-agent-design §7.2)")
