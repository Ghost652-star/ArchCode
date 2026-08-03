"""危险命令检测器 —— 权限判定第一层。

用 regex 匹配已知危险模式（rm -rf /、mkfs、fork bomb 等），
命中 → 直接 deny（作为 tool_result 返回给 LLM），不需要再走后续层。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple


# ---------------------------------------------------------------------------
# 危险命令模式
# ---------------------------------------------------------------------------


@dataclass
class _Pattern:
    regex: re.Pattern[str]
    reason: str


_DANGEROUS_PATTERNS: list[_Pattern] = [
    # --- 文件系统破坏 ---
    _Pattern(
        re.compile(
            r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/"
            r"|\brm\s+.*--no-preserve-root",
            re.IGNORECASE,
        ),
        "rm -rf / would recursively delete the root filesystem",
    ),
    _Pattern(
        re.compile(
            r"\bmkfs\b",
            re.IGNORECASE,
        ),
        "mkfs creates a filesystem, potentially overwriting data",
    ),
    _Pattern(
        re.compile(
            r"\bdd\s+.*of=/dev/",
            re.IGNORECASE,
        ),
        "dd writing directly to a device file can corrupt disks",
    ),
    _Pattern(
        re.compile(
            r"\bchmod\s+-R\s+777\s+/"
            r"|\bchmod\s+777\s+/",
            re.IGNORECASE,
        ),
        "chmod 777 on root makes the entire system world-writable",
    ),
    _Pattern(
        re.compile(
            r">\s*/dev/sd[a-z]",
            re.IGNORECASE,
        ),
        "redirecting output to a device file can overwrite disk contents",
    ),
    # --- Fork bomb ---
    _Pattern(
        re.compile(
            r":\(\)\s*\{[^}]*:\|[^}]*&\s*\}|fork\s*bomb|\bforkbomb\b",
            re.IGNORECASE,
        ),
        "fork bomb would exhaust system resources by spawning processes",
    ),
    # --- 远程代码执行 ---
    _Pattern(
        re.compile(
            r"\bcurl\b.*\|\s*(?:ba)?sh\b|\bcurl\b.*\|\s*(?:ba)?sh\b",
            re.IGNORECASE,
        ),
        "curl piped to shell executes untrusted remote code without review",
    ),
    _Pattern(
        re.compile(
            r"\bwget\b.*\|\s*(?:ba)?sh\b|\bwget\b.*-O-\s+\S+\s*\|\s*(?:ba)?sh\b",
            re.IGNORECASE,
        ),
        "wget piped to shell executes untrusted remote code without review",
    ),
    # --- 磁盘/分区破坏 ---
    _Pattern(
        re.compile(
            r"\b(?:fdisk|parted|gdisk)\b.*\b/dev/",
            re.IGNORECASE,
        ),
        "disk partitioning tool targeting a device can destroy partition tables",
    ),
    _Pattern(
        re.compile(
            r"\bformat\s+[A-Z]:|\bdel\s+/[Ff]\s+/[Ss]\s+[A-Z]:\\",
            re.IGNORECASE,
        ),
        "Windows destructive command — format or recursive delete from drive root",
    ),
    # --- 权限提升 + 危险组合 ---
    _Pattern(
        re.compile(
            r"\bsudo\s+su\b",
            re.IGNORECASE,
        ),
        "sudo su switches to root without accountability",
    ),
]


# ---------------------------------------------------------------------------
# 检测结果
# ---------------------------------------------------------------------------


class DetectionResult(NamedTuple):
    hit: bool
    reason: str | None


# ---------------------------------------------------------------------------
# DangerousCommandDetector
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 安全命令白名单（只读、无副作用）
# ---------------------------------------------------------------------------

_SAFE_COMMANDS = frozenset({
    "ls", "dir", "pwd", "echo", "cat", "head", "tail", "wc",
    "find", "which", "whereis", "whoami", "hostname", "uname",
    "date", "cal", "uptime", "df", "du", "free", "env", "printenv",
    "file", "stat", "readlink", "realpath", "basename", "dirname",
    "sort", "uniq", "tr", "cut", "awk", "sed", "grep", "egrep", "fgrep",
    "diff", "comm", "tee", "xargs", "true", "false", "test",
    "git status", "git log", "git diff", "git show", "git branch",
    "git tag", "git remote", "git rev-parse", "git ls-files",
    "git blame", "git stash list",
    "go version", "go env",
    "node -v", "npm -v", "npx",
    "python --version", "pip list",
    "cargo --version", "rustc --version",
    "java -version", "java --version",
})


def is_safe_command(command: str) -> bool:
    """快速判断命令是否安全（纯只读、无管道/重定向/命令替换）。"""
    trimmed = command.strip()
    if not trimmed:
        return False
    for ch in ("|", ";", "&&", ">", "$(", "`"):
        if ch in trimmed:
            return False
    for safe in _SAFE_COMMANDS:
        if trimmed == safe or trimmed.startswith(safe + " "):
            return True
    return False


# ---------------------------------------------------------------------------
# DangerousCommandDetector
# ---------------------------------------------------------------------------


class DangerousCommandDetector:
    """检测命令行参数中的已知危险模式。

    使用场景：
        permissions/checker.py 在权限判定第一层调用此检测器：
        1. 提取 tool 的 content 字段（Bash → command, WriteFile → file_path...）
        2. 调用 detector.detect(content)
        3. 若 hit → deny，返回 tool_result（reason 作为错误信息）
        4. 若 miss → 继续走 sandbox / rules / mode 矩阵层
    """

    def detect(self, command: str) -> DetectionResult:
        """扫描命令字符串，返回 (hit, reason)。"""
        for pattern in _DANGEROUS_PATTERNS:
            if pattern.regex.search(command):
                return DetectionResult(hit=True, reason=pattern.reason)
        return DetectionResult(hit=False, reason=None)
