"""Agent 定义的数据模型(sub-agent-design §4.1:frontmatter 字段 + 派生信息)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentDef:
    """单个子 agent 定义的内存形态。

    `model` 留白("" / "inherit")= 沿用主对话 provider(§3.2 selectLLM 兜底);
    不绑定任何厂商的专有模型命名(§4.4.1),其他值按别名在 selectLLM 时解析。
    """

    agent_type: str                            # frontmatter name = subagent_type 取值
    when_to_use: str                           # frontmatter description(给主 LLM 的选用依据)
    system_prompt: str                         # Markdown body(子 agent 的身份,§4.1)
    tools: list[str] = field(default_factory=list)            # 白名单:只允许这些工具
    disallowed_tools: list[str] = field(default_factory=list)  # 黑名单:禁用这些工具
    model: str = "inherit"                     # "" / "inherit" = 沿用父;别名解析在 selectLLM
    max_turns: int = 50                        # 轮次预算
    permission_mode: str = "default"           # default / acceptEdits / dontAsk(§10)
    background: bool = False                   # 定义式转后台开关①(§8)
    isolation: str = ""                        # "" / worktree(worktree 本批只落 stub,§7.2)
    file_path: Path | None = None              # 定义文件路径(热重载用,§4.2)
    source: str = "builtin"                    # project / user / builtin
