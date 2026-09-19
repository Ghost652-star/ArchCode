"""可插拔技能系统(第一阶段:单文件 Skill,见 workstatus/skills-design.md)。

组件:
- models.py    SkillManifest(frontmatter 元数据 + checksum)
- loader.py    SkillLoader:三层扫描 + seen 遮蔽诊断 + catalog 文本
- executor.py  SkillExecutor:激活事务(读正文/渲染/校验/重钉/登记)
- tools.py     LoadSkillTool(模型入口)
- builtin/     内置 Skill 包数据占位(当前为空)

设计定稿见 workstatus/skills-design.md;架构细节见 docs/implemented-architecture-design.md §12。
"""

from archcode.skills.executor import SkillExecutor, substitute_arguments
from archcode.skills.loader import SkillLoader, parse_frontmatter
from archcode.skills.models import SkillManifest
from archcode.skills.tools import LoadSkillTool

__all__ = [
    "LoadSkillTool",
    "SkillExecutor",
    "SkillLoader",
    "SkillManifest",
    "parse_frontmatter",
    "substitute_arguments",
]
