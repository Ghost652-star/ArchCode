"""文件日志系统 —— TUI 运行时看不到控制台，所有输出落盘到 .archcode/archcode.log。

在 __main__.py 的 main() 最开头调用 setup()。

覆盖三路输出：
1. root logger → FileHandler（logging.debug/info/warning/error 全部落盘）
2. sys.excepthook → 未捕获异常也写进 log
3. sys.stderr → 重定向到 log 文件（print 到 stderr、Textual 的错误输出都进文件）

注意：不碰 sys.stdout（Textual 需要真实 stdout 画界面）。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_LOGFILE_NAME = "archcode.log"


def _log_path() -> Path:
    return Path(".archcode") / _LOGFILE_NAME


def setup(level: int = logging.DEBUG) -> Path:
    """初始化文件日志。返回日志文件路径。可重复调用（幂等）。"""
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # 只挂一个 FileHandler，避免和默认 stderr StreamHandler 双写
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    # root 用 INFO：过滤第三方 DEBUG 噪音（markdown_it / httpcore / httpx…）
    root.setLevel(logging.INFO)

    # 我们自己（archcode.*）保持详细级别
    archcode_logger = logging.getLogger("archcode")
    archcode_logger.setLevel(level)

    # 已知吵的第三方 logger 压到 WARNING，避免刷屏
    for noisy in ("markdown_it", "httpcore", "httpx", "openai", "httpcore.http11"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    fh = logging.FileHandler(path, encoding="utf-8", delay=True)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # 未捕获异常落盘
    def _excepthook(etype, evalue, tb) -> None:
        logging.getLogger("unhandled").critical(
            "Unhandled exception", exc_info=(etype, evalue, tb)
        )

    sys.excepthook = _excepthook

    # stderr 重定向到 log 文件（保持 stdout 给 Textual 用）
    try:
        sys.stderr = path.open("a", encoding="utf-8")
    except OSError:
        pass

    logging.debug("=== ArchCode 启动，日志写入 %s ===", path.resolve())
    return path
