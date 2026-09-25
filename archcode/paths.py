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


def project_skills_dir(work_dir: str | Path) -> Path:
    """项目级 Skill 目录(优先级最高,随 Git 共享)。"""
    return project_data_dir(work_dir) / "skills"


def application_skills_dir() -> Path:
    """用户级 Skill 目录(个人跨项目;本地学习项目,不落 C 盘)。"""
    return application_data_dir() / "skills"


def project_agents_dir(work_dir: str | Path) -> Path:
    """项目级 agent 定义目录(优先级最高,随 Git 共享;sub-agent-design §4.2)。"""
    return project_data_dir(work_dir) / "agents"


def application_agents_dir() -> Path:
    """用户级 agent 定义目录(个人跨项目;本地学习项目不落 C 盘,§4.2)。"""
    return application_data_dir() / "agents"


def debug_log_path(work_dir: str | Path) -> Path:
    """项目级调试日志文件(日志最小集,deferred-designs #4)。

    落在 `<work_dir>/.archcode/debug.log`——按路径纪律是项目级数据,绝不进源码根。
    目录已存在(project_data_dir 会 mkdir),此处只给路径。
    """
    return project_data_dir(work_dir) / "debug.log"
