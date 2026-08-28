"""ArchCode 应用级与项目级数据目录的统一定义。"""

from __future__ import annotations

from pathlib import Path


def application_root() -> Path:
    """返回 ArchCode 当前源码/安装根目录。"""
    return Path(__file__).resolve().parent.parent


def application_data_dir() -> Path:
    """返回当前 ArchCode 环境的用户级数据目录。"""
    return application_root() / ".archcode"


def project_data_dir(work_dir: str | Path) -> Path:
    """返回启动时指定工作项目的数据目录。"""
    return Path(work_dir).resolve() / ".archcode"
