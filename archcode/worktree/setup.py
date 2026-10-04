"""创建后设置(worktree-design §2.3.4):补齐 git worktree add 不搬运的运行时环境。

根因:`git worktree add` 只检出 tracked 文件,gitignore 的本地配置、依赖目录、
git 配置层面的 hooks 都不在——新建副本"代码全、环境缺"。四项全部 best-effort:
失败只记警告,不中断创建(环境缺失影响的是子 agent 某项能力,不是"建好了"这个事实)。
"""

from __future__ import annotations

import fnmatch
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 60


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("git 命令失败(best-effort 跳过): %s", e)
        return None


def perform_post_creation_setup(
    repo_root: Path,
    wt_path: str,
    symlink_directories: list[str] | None = None,
) -> None:
    """四项初始化(仅新建执行;快速恢复路径跳过——残留目录的环境本来就位)。"""
    _setup_hooks_path(repo_root, wt_path)
    _setup_dependency_links(repo_root, wt_path, symlink_directories or [])
    _copy_included_files(repo_root, wt_path)


def _setup_hooks_path(repo_root: Path, wt_path: str) -> None:
    """主仓库用 husky/自定义 hooks 目录时,把 hooksPath 显式写入副本专属配置
    (worktree 共享 .git/config,直接写会污染主仓库——用 --worktree 隔离)。"""
    wt = Path(wt_path)
    if (repo_root / ".husky").is_dir():
        hooks_dir = repo_root / ".husky"
    elif (repo_root / ".git" / "hooks").is_dir():
        hooks_dir = repo_root / ".git" / "hooks"
    else:
        return
    r = _git(wt, "config", "extensions.worktreeConfig", "true")
    if r is None or r.returncode != 0:
        logger.warning("hooksPath 设置跳过: 无法开启 worktreeConfig")
        return
    r = _git(wt, "config", "--worktree", "core.hooksPath", str(hooks_dir))
    if r is None or r.returncode != 0:
        logger.warning("core.hooksPath 写入失败(best-effort 跳过)")


def _link_directory(source: Path, link: Path) -> None:
    """目录联接优先(Windows 非特权账户不能建 symlink);失败只警告。"""
    try:
        if os.name == "nt":
            import _winapi  # stdlib 内部但长期稳定;junction 不需要特权

            _winapi.CreateJunction(str(source), str(link))
        else:
            os.symlink(str(source), str(link), target_is_directory=True)
        logger.info("依赖目录已链接: %s -> %s", link, source)
    except (OSError, AttributeError) as e:
        logger.warning("依赖目录链接失败(best-effort 跳过): %s: %s", link, e)


def _setup_dependency_links(repo_root: Path, wt_path: str, symlink_directories: list[str]) -> None:
    """大依赖目录(node_modules/.venv 等)共享主仓库的一份,零复制。
    目录列表来自配置——不同项目依赖结构不同,不写死。"""
    for rel in symlink_directories:
        source = repo_root / rel
        link = Path(wt_path) / rel
        if not source.is_dir():
            continue
        if link.exists() or link.is_symlink():
            continue
        link.parent.mkdir(parents=True, exist_ok=True)
        _link_directory(source, link)


def _copy_included_files(repo_root: Path, wt_path: str) -> None:
    """.worktreeinclude(gitignore 语法)声明哪些被忽略的文件需要复制到副本(典型 .env)。"""
    include_file = repo_root / ".worktreeinclude"
    if not include_file.is_file():
        return
    try:
        patterns = [
            line.strip()
            for line in include_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not patterns:
            return
        r = _git(
            repo_root,
            "ls-files", "--others", "--ignored", "--exclude-standard", "--directory",
        )
        if r is None or r.returncode != 0:
            logger.warning("被忽略文件列表获取失败(best-effort 跳过)")
            return
        copied = 0
        for rel in r.stdout.splitlines():
            rel = rel.rstrip("/")
            if not rel or not any(fnmatch.fnmatch(rel, p) or rel == p for p in patterns):
                continue
            src = repo_root / rel
            dst = Path(wt_path) / rel
            if not src.is_file() or dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            copied += 1
        if copied:
            logger.info(".worktreeinclude 复制了 %d 个文件到副本", copied)
    except OSError as e:
        logger.warning(".worktreeinclude 处理失败(best-effort 跳过): %s", e)
