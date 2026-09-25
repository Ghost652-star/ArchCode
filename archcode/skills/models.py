"""Skill 的数据模型(渐进披露第一层:只有 frontmatter 元数据)。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SkillManifest:
    """单个单文件 Skill 的元数据。

    字段来源:SKILL.md 的 YAML frontmatter + 文件本身的派生信息。
    正文不在这里——激活时才读(渐进披露第二层,见 skills-design.md 0.2)。
    """

    name: str          # ^[a-z0-9]+(-[a-z0-9]+)*$,同时是 /命令 名
    description: str   # 目录/补全里展示的一句话
    path: Path         # SKILL.md 文件路径(激活时从盘上现读,改了无需重启)
    source: str        # "project" | "user" | "builtin"(三层来源,0.1)
    checksum: str      # sha256(SKILL.md 字节),RecoveryState 登记与变更检测用
    allowed_tools: tuple[str, ...] = ()  # 可选的可见性收窄声明(0.6);缺省 = 不收窄
    is_directory: bool = False           # 目录型(SKILL.md + tool.json + references/)
    skill_dir: Path | None = None        # 目录型时的 Skill 根目录(tool.json 所在)
    mode: str = "inline"  # "inline"(钉 SOP 进主对话) | "fork"(创建一个子 agent 执行该 skill,随 agents/ 落地)
    context: str = "recent"  # fork 携带父上下文档位:"full" | "recent"(默认) | "none"(仅 fork 生效,inline 忽略)
