"""Agent Teams(多 Agent 团队协作,agent-teams-design 全定稿)。

lead(TeamCreate)建队 → 拆共享任务 → spawn 队员(独立副本 + 协调工具 +
文件邮箱)→ 队员自主认领/横向通信 → 空闲续写 → 收敛(lead git 合并)→
清理(TeamDelete)。协调能力以工具注入队员,框架不设调度器。
对外主要入口:TeamManager(建队/删队/花名册/投递;队员执行由 agent_tool 协作)。
"""

from archcode.teams.manager import TeamManager
from archcode.teams.models import MailboxMessage, Team, TeamError, TeamTask, Teammate
from archcode.teams.prompts import COORDINATOR_PROMPT, LEAD_COORDINATION_HINT, TEAMMATE_APPENDIX

__all__ = [
    "TeamManager",
    "Team",
    "Teammate",
    "TeamTask",
    "MailboxMessage",
    "TeamError",
    "TEAMMATE_APPENDIX",
    "LEAD_COORDINATION_HINT",
    "COORDINATOR_PROMPT",
]
