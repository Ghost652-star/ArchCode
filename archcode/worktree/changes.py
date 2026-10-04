"""worktree 改动检测(worktree-design §6 双判据 / §5.2 fail-closed)。

判定"子 agent 有没有留下工作成果":
- uncommitted:`git status --porcelain` 非空行数;
- new_commits:`git rev-list --count <head_commit>..HEAD`(基线之后的新提交)。
任一 > 0 即视为有改动。git 命令本身执行失败 → 默认视为有改动(fail-closed:
宁可多保留一个目录,不误删)。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_TIMEOUT = 30


def _run_git_out(repo: str | Path, *args: str) -> str | None:
    """跑 git 返回 stdout;命令失败返回 None(由调用方 fail-closed 处理)。"""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def has_worktree_changes(wt_path: str, head_commit: str) -> bool:
    """双判据:未提交修改 或 基线后新提交。git 失败默认 True(§5.2)。"""
    status = _run_git_out(wt_path, "status", "--porcelain")
    if status is None:
        return True
    if status.strip():
        return True
    if not head_commit:
        return True
    count = _run_git_out(wt_path, "rev-list", "--count", f"{head_commit}..HEAD")
    if count is None:
        return True
    try:
        return int(count.strip()) > 0
    except ValueError:
        return True


def has_unpushed_commits(wt_path: str) -> bool:
    """HEAD 上是否存在远程没有的 commit(已提交未推送)。git 失败默认 True。"""
    out = _run_git_out(wt_path, "rev-list", "--max-count=1", "HEAD", "--not", "--remotes")
    if out is None:
        return True
    return bool(out.strip())
