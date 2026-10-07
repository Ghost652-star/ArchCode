"""Agent Teams 的数据模型(agent-teams-design §1)。

- Team:花名册(名称 + 负责人 + 队员列表),持久化到团队目录 config.json;
- Teammate:花名册的一行;is_active 是后台任务状态的投影(§4.4),恢复时重算;
- TeamTask:共享任务(pending/in_progress/completed + 依赖 blocks/blocked_by);
- MailboxMessage:邮箱里的一条消息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


class TeamError(Exception):
    """teams 管理错误(建队/删队/spawn/寻址的失败出口,错误文本直接返回 LLM)。"""


BACKEND_TYPES = Literal["tmux", "iterm2", "in-process"]
TASK_STATUSES = Literal["pending", "in_progress", "completed"]


@dataclass
class Team:
    """一个团队:名称 + 负责人 + 花名册(§4.1)。lead = 主 agent 本身,不另起实例。"""

    name: str
    lead_agent_id: str
    members: list["Teammate"] = field(default_factory=list)
    config_path: str = ""
    description: str = ""
    default_agent_type: str = ""  # 全队默认队员类型(TeamCreate 的 agent_type)

    def teammate_by_name(self, name: str) -> "Teammate | None":
        for m in self.members:
            if m.name == name:
                return m
        return None


@dataclass
class Teammate:
    """花名册的一行(§4.2)。is_active 投影自后台任务状态;终止 = 从 members 移除,不留墓碑。"""

    name: str
    agent_id: str
    agent_type: str
    model: str = ""
    worktree_path: str = ""
    backend_type: BACKEND_TYPES = "in-process"
    is_active: bool | None = None
    plan_mode_required: bool = False


@dataclass
class TeamTask:
    """共享任务(§3.2):团队 tasks.json 的一行;依赖完成即解锁(认领判定即时算)。"""

    id: str
    subject: str
    description: str = ""
    status: TASK_STATUSES = "pending"
    owner: str = ""  # 认领队员的 name(空 = 未认领)
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass
class MailboxMessage:
    """邮箱里的一条消息(§5.2/§5.3)。text 类型必须带 summary(UI 预览)。"""

    msg_id: str
    from_name: str
    to_name: str
    message_type: Literal["text", "shutdown_request", "shutdown_response", "plan_approval_response"] = "text"
    content: str = ""
    summary: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
