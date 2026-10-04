"""过期 worktree 的后台清理(worktree-design §6.2)。

与 auto_cleanup 的分工:auto_cleanup 管正常退出(即时、有基线);本模块管异常
退出——进程崩溃/被强杀留下的孤儿。触发是系统级保洁:应用启动时挂常驻任务,
周期扫描统一落点目录(磁盘是唯一事实源,崩溃后内存账本已丢)。agent/LLM 不参与。

五层漏斗,任何一层不通过即跳过(fail-closed:检查不出来 = 不删):
临时命名模式 → 在用保护 → mtime 过期 → 变更检查 → 未推送 commit 检查。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from archcode.worktree.changes import has_unpushed_commits, has_worktree_changes
from archcode.worktree.manager import WorktreeManager

logger = logging.getLogger(__name__)

# 系统创建的临时 worktree 命名模式;用户起的名字不匹配 → 永不自动清理。
# 与 manager.generate_worktree_name 同步定义(命名约定即清理判据,§6.2)。
EPHEMERAL_PATTERNS = [
    re.compile(r"^agent-[0-9a-f]{8}$"),
]


def _is_ephemeral(name: str) -> bool:
    return any(p.match(name) for p in EPHEMERAL_PATTERNS)


async def cleanup_stale_worktrees(manager: WorktreeManager, cutoff_hours: int) -> int:
    cutoff = datetime.now() - timedelta(hours=cutoff_hours)
    worktree_dir = Path(manager.worktree_dir)
    if not worktree_dir.exists():
        return 0

    removed = 0
    for entry in worktree_dir.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name

        # 第 1 层:只清系统创建的临时 worktree;用户命名的永不自动清理
        if not _is_ephemeral(name):
            continue
        # 第 2 层:当前会话正用着的不动
        if manager.current_session and manager.current_session.worktree_name == name:
            continue
        # 第 3 层:未过期的不动
        try:
            if datetime.fromtimestamp(entry.stat().st_mtime) > cutoff:
                continue
        except OSError:
            continue
        # 第 4 层:目录残缺(HEAD 读不出)或有改动的不动
        head_sha = WorktreeManager.read_worktree_head_sha(str(entry))
        if head_sha is None:
            continue
        if has_worktree_changes(str(entry), head_sha):
            continue
        # 第 5 层:有已提交未推送 commit 的不动(宁多占磁盘,不丢工作)
        if has_unpushed_commits(str(entry)):
            continue

        try:
            async with manager.lock:
                wt = manager.active.get(name)
                if wt is not None:
                    await manager._remove_worktree(name, wt)
                else:
                    # 崩溃孤儿:不在账本里,直接走 git
                    r = await manager._run_git(["worktree", "remove", "--force", str(entry)])
                    if r.returncode != 0:
                        logger.warning("孤儿 worktree 删除失败: %s", r.stderr.strip())
                        continue
                    await asyncio.sleep(0.1)
                    await manager._run_git(["branch", "-D", f"worktree-{name}"])
            removed += 1
            logger.info("后台清理删除过期 worktree: %s", name)
        except Exception as e:  # 单条失败继续下一条(best-effort)
            logger.warning("后台清理失败(跳过 %s): %s", name, e)

    return removed


async def start_stale_cleanup_task(
    manager: WorktreeManager,
    interval: int,
    cutoff_hours: int,
) -> None:
    """常驻循环:由装配层 create_task 启动,应用关闭时 cancel。"""
    while True:
        await asyncio.sleep(interval)
        try:
            count = await cleanup_stale_worktrees(manager, cutoff_hours)
            if count:
                logger.info("后台清理共删除 %d 个过期 worktree", count)
        except Exception as e:
            logger.warning("后台清理异常(下轮继续): %s", e)
