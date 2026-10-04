"""Git Worktree 隔离(worktree-design 全定稿)。

子 agent 带 isolation:worktree 时,工作目录为独立副本(共享仓库、隔离文件);
跑完 auto_cleanup 验货:无改动自动删,有改动保留给主 agent review。
对外主要入口:WorktreeManager(create/enter/exit/auto_cleanup/restore_session)、
generate_worktree_name、build_worktree_notice。
"""

from archcode.worktree.manager import WorktreeManager, generate_worktree_name
from archcode.worktree.integration import build_worktree_notice
from archcode.worktree.models import Worktree, WorktreeError, WorktreeSession
from archcode.worktree.slug import flatten_slug, validate_slug

__all__ = [
    "WorktreeManager",
    "generate_worktree_name",
    "build_worktree_notice",
    "Worktree",
    "WorktreeError",
    "WorktreeSession",
    "validate_slug",
    "flatten_slug",
]
