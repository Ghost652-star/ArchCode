"""worktree 会话持久化(worktree-design §2.7)。

current_session 落盘到 <落点>/worktree_session.json:enter 写入、exit 写空、
进程重启后 restore_session 从这里捡回。退出时把盘上记录清空是 resume 正确
工作的前提——盘上留着记录会被恢复逻辑当成"未结束的会话"。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from archcode.worktree.models import WorktreeSession

logger = logging.getLogger(__name__)

_SESSION_FILE = "worktree_session.json"


def session_file_path(base_dir: str | Path) -> Path:
    return Path(base_dir) / _SESSION_FILE


def save_worktree_session(base_dir: str | Path, session: WorktreeSession | None) -> None:
    """写盘;None = 写空对象(擦账本的两个动作之一,§6)。"""
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if session is not None:
        data = {
            "original_cwd": session.original_cwd,
            "worktree_path": session.worktree_path,
            "worktree_name": session.worktree_name,
            "original_branch": session.original_branch,
            "original_head_commit": session.original_head_commit,
            "session_id": session.session_id,
            "hook_based": session.hook_based,
        }
    path = session_file_path(base)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_worktree_session(base_dir: str | Path) -> WorktreeSession | None:
    """读盘;文件缺失/损坏/为空 → None。"""
    path = session_file_path(base_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("worktree session 文件读取失败,忽略: %s", e)
        return None
    if not data:
        return None
    return WorktreeSession(
        original_cwd=data.get("original_cwd", ""),
        worktree_path=data.get("worktree_path", ""),
        worktree_name=data.get("worktree_name", ""),
        original_branch=data.get("original_branch", ""),
        original_head_commit=data.get("original_head_commit", ""),
        session_id=data.get("session_id", ""),
        hook_based=bool(data.get("hook_based", False)),
    )
