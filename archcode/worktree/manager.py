"""WorktreeManager(worktree-design §2.2/§2.3.3/§2.6)。

状态元素:repo_root / worktree_dir / lock / active / current_session(file_cache 预留槽,
待 FileCache 类落地后挂入并在 enter/exit 清理)。
并发语义(§2.5 方案 3 混合):active 是集合,服务子 agent spawn 并行(不经过
current_session);current_session 仅服务人手动进出的交互场景。
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import subprocess
from pathlib import Path

from archcode.worktree.models import Worktree, WorktreeError, WorktreeSession
from archcode.worktree.session import load_worktree_session, save_worktree_session
from archcode.worktree.slug import flatten_slug, validate_slug

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 60
_LOCKFILE_WAIT_S = 0.1  # 两条 git 命令贴太近会撞 lockfile 未释放(§6 删除序列)


class WorktreeManager:
    """进程内单例(装配层构造,§4 对接清单)。"""

    def __init__(self, work_dir: str | Path) -> None:
        self.repo_root = Path(work_dir).resolve()
        self.worktree_dir = self.repo_root / ".archcode" / "worktrees"
        self.lock = asyncio.Lock()
        self.active: dict[str, Worktree] = {}
        self.current_session: WorktreeSession | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._cleanup_interval: int = 3600
        self._cleanup_cutoff: int = 24

    # ------------------------------------------------------------------
    # git 子进程:唯一出口,纪律见 §2.3.3(后台进程宁可 fast-fail 不可挂起)
    # ------------------------------------------------------------------

    @staticmethod
    def _git_env() -> dict[str, str]:
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"  # 需要凭证时立即失败,不许在终端等输入
        env["GIT_ASKPASS"] = ""           # 同时掐掉 GUI 凭证弹窗通道
        return env

    def _run_git_sync(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or str(self.repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT,
            stdin=subprocess.DEVNULL,
            env=self._git_env(),
        )

    async def _run_git(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        return await asyncio.to_thread(self._run_git_sync, args, cwd)

    # ------------------------------------------------------------------
    # 快速恢复:纯文件系统读取,不启动 git 子进程(§2.6)
    # ------------------------------------------------------------------

    @staticmethod
    def read_worktree_head_sha(wt_path: str) -> str | None:
        """读出副本 HEAD 的 commit SHA;目录无效/读不出 → None。

        副本的 .git 是指针文件(一行 gitdir: <主仓库>/.git/worktrees/<name>),
        顺着指针找 gitdir,再解析 HEAD(直接 SHA 或符号引用 ref: refs/...,
        后者依次查 gitdir/refs、commondir/refs、commondir/packed-refs)。
        """
        try:
            dot_git = Path(wt_path) / ".git"
            if not dot_git.is_file():
                return None
            content = dot_git.read_text(encoding="utf-8").strip()
            if not content.startswith("gitdir:"):
                return None
            gitdir = Path(content[len("gitdir:"):].strip())
            if not gitdir.is_absolute():
                gitdir = (Path(wt_path) / gitdir).resolve()
            head_file = gitdir / "HEAD"
            if not head_file.is_file():
                return None
            head = head_file.read_text(encoding="utf-8").strip()
            if not head:
                return None
            if not head.startswith("ref:"):
                return head  # detached:HEAD 直接就是 SHA

            ref = head[len("ref:"):].strip()
            # 候选 1:gitdir 自己的 refs(worktree 专属引用)
            candidate = gitdir / ref
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8").strip()
            # 候选 2:commondir(共享 refs 在主仓库)
            commondir_file = gitdir / "commondir"
            if not commondir_file.is_file():
                return None
            commondir = (gitdir / commondir_file.read_text(encoding="utf-8").strip()).resolve()
            candidate = commondir / ref
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8").strip()
            # 候选 3:commondir/packed-refs
            packed = commondir / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0]
            return None
        except OSError:
            return None

    # ------------------------------------------------------------------
    # create(§2.3.3 六步,全程锁内)
    # ------------------------------------------------------------------

    async def create(self, name: str, base_branch: str = "HEAD") -> Worktree:
        err = validate_slug(name)
        if err:
            raise WorktreeError(err)

        async with self.lock:
            if name in self.active:
                raise WorktreeError(f"worktree already exists: {name}")

            flat_slug = flatten_slug(name)
            wt_path = str(self.worktree_dir / flat_slug)
            branch_name = f"worktree-{flat_slug}"

            # 步骤 4:快速恢复——目录在且 HEAD 可读 → 复用,不重复 git worktree add
            head_sha = self.read_worktree_head_sha(wt_path)
            if head_sha is not None:
                logger.info("worktree 快速恢复: 复用已存在目录 %s", wt_path)
                wt = Worktree(
                    name=name,
                    path=wt_path,
                    branch=branch_name,
                    based_on=base_branch,
                    head_commit=head_sha,
                )
                self.active[name] = wt
                return wt

            self.worktree_dir.mkdir(parents=True, exist_ok=True)
            result = await self._run_git(
                ["worktree", "add", "-B", branch_name, wt_path, base_branch]
            )
            if result.returncode != 0:
                raise WorktreeError(f"git worktree add failed: {result.stderr.strip()}")

            # 创建后设置(best-effort,仅新建执行;快速恢复路径跳过,§2.3.4)
            from archcode.worktree.setup import perform_post_creation_setup

            perform_post_creation_setup(self.repo_root, wt_path)

            head_sha = self.read_worktree_head_sha(wt_path) or ""
            wt = Worktree(
                name=name,
                path=wt_path,
                branch=branch_name,
                based_on=base_branch,
                head_commit=head_sha,
            )
            self.active[name] = wt
            logger.info("worktree created: %s -> %s (%s)", name, wt_path, branch_name)
            return wt

    # ------------------------------------------------------------------
    # 删除序列(§6)
    # ------------------------------------------------------------------

    async def _remove_worktree(self, name: str, wt: Worktree) -> None:
        result = await self._run_git(["worktree", "remove", "--force", wt.path])
        if result.returncode != 0:
            logger.warning("git worktree remove 失败(继续删分支): %s", result.stderr.strip())
        await asyncio.sleep(_LOCKFILE_WAIT_S)  # 等 git lockfile 释放(经验值)
        branch_result = await self._run_git(["branch", "-D", wt.branch])
        if branch_result.returncode != 0:
            logger.warning("git branch -D 失败: %s", branch_result.stderr.strip())
        self.active.pop(name, None)
        logger.info("worktree removed: %s", name)

    # ------------------------------------------------------------------
    # enter / exit(§2.1 记账不切 cwd;§6 变更保护 + 擦账本)
    # ------------------------------------------------------------------

    def _get_current_branch(self) -> str:
        r = self._run_git_sync(["rev-parse", "--abbrev-ref", "HEAD"])
        return r.stdout.strip() if r.returncode == 0 else ""

    def _get_head_commit(self) -> str:
        r = self._run_git_sync(["rev-parse", "HEAD"])
        return r.stdout.strip() if r.returncode == 0 else ""

    async def enter(self, name: str, session_id: str = "") -> WorktreeSession:
        """手动进入场景(交互工具用,spawn 隔离不走这里)。记账,不切进程 cwd。"""
        async with self.lock:
            wt = self.active.get(name)
            if wt is None:
                raise WorktreeError(f"not found: {name}")
            session = WorktreeSession(
                original_cwd=str(self.repo_root),
                worktree_path=wt.path,
                worktree_name=wt.name,
                original_branch=self._get_current_branch(),
                original_head_commit=self._get_head_commit(),
                session_id=session_id,
            )
            self.current_session = session
            save_worktree_session(self.worktree_dir, session)
            return session

    async def exit(
        self,
        name: str,
        action: str = "keep",
        discard_changes: bool = False,
    ) -> None:
        """退出:变更保护 → 擦账本 → 按 action 处置(§6)。"""
        async with self.lock:
            wt = self.active.get(name)
            if wt is None:
                raise WorktreeError(f"not found: {name}")

            if action == "remove" and not discard_changes:
                from archcode.worktree.changes import has_worktree_changes

                if has_worktree_changes(wt.path, wt.head_commit):
                    raise WorktreeError(
                        "worktree has changes, set discard_changes=true to force"
                    )

            # 擦账本:内存 None + 盘上写空——resume 正确工作的前提
            self.current_session = None
            save_worktree_session(self.worktree_dir, None)

            if action == "remove":
                await self._remove_worktree(name, wt)

    # ------------------------------------------------------------------
    # auto_cleanup(§6.1:子 agent 正常退出的即时收尾)
    # ------------------------------------------------------------------

    async def auto_cleanup(self, name: str, head_commit: str | None = None) -> dict:
        """有改动 → 保留(返回路径+分支给主 agent);无改动 → 删。"""
        async with self.lock:
            wt = self.active.get(name)
            if wt is None:
                raise WorktreeError(f"not found: {name}")

        from archcode.worktree.changes import has_worktree_changes

        baseline = head_commit or wt.head_commit
        if has_worktree_changes(wt.path, baseline):
            logger.info("auto_cleanup 保留: %s(有改动)", name)
            return {"kept": True, "path": wt.path, "branch": wt.branch}
        async with self.lock:
            wt = self.active.get(name)
            if wt is not None:
                await self._remove_worktree(name, wt)
        logger.info("auto_cleanup 删除: %s(无改动)", name)
        return {"kept": False}

    # ------------------------------------------------------------------
    # 恢复(§2.7 四步)与查询
    # ------------------------------------------------------------------

    def restore_session(self) -> WorktreeSession | None:
        """启动时从盘上捡回未结束的会话;目录失效 → 清盘返回 None(宁丢不给僵尸)。"""
        session = load_worktree_session(self.worktree_dir)
        if session is None:
            return None
        head_sha = self.read_worktree_head_sha(session.worktree_path)
        if head_sha is None:
            logger.warning("worktree session 目录已失效,清除记录: %s", session.worktree_path)
            save_worktree_session(self.worktree_dir, None)
            return None
        flat_slug = flatten_slug(session.worktree_name)
        wt = Worktree(
            name=session.worktree_name,
            path=session.worktree_path,
            branch=f"worktree-{flat_slug}",
            based_on="",
            head_commit=head_sha,
        )
        self.active[wt.name] = wt
        self.current_session = session
        logger.info("worktree session 恢复: %s -> %s", wt.name, wt.path)
        return session

    def list_worktrees(self) -> list[Worktree]:
        return list(self.active.values())

    def get_current_session(self) -> WorktreeSession | None:
        return self.current_session

    # ------------------------------------------------------------------
    # 后台清理任务的启停(§6.2;装配层在事件循环就绪处调用)
    # ------------------------------------------------------------------

    def ensure_cleanup_task(self, interval: int | None = None, cutoff_hours: int | None = None) -> None:
        """惰性启动常驻清理任务(已有在跑的则跳过);需在运行中的事件循环内调用。

        参数缺省时用装配层存下的配置值(默认 1 小时/次、24 小时过期,§6.2)。
        """
        from archcode.worktree.cleanup import start_stale_cleanup_task

        interval = interval if interval is not None else self._cleanup_interval
        cutoff_hours = cutoff_hours if cutoff_hours is not None else self._cleanup_cutoff
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(
            start_stale_cleanup_task(self, interval, cutoff_hours)
        )
        logger.info("worktree 后台清理任务已启动: interval=%ss cutoff=%sh", interval, cutoff_hours)

    async def shutdown_cleanup_task(self) -> None:
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        self._cleanup_task = None


def generate_worktree_name() -> str:
    """spawn 用的唯一名:agent-<8位hex>。命名模式即后台清理的临时标记
    (cleanup.EPHEMERAL_PATTERNS),两处必须同步定义(§6.2)。"""
    return f"agent-{secrets.token_hex(4)}"
