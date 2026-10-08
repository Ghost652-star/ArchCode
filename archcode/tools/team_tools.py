"""团队工具(agent-teams-design §4.5/§5):TeamCreate / TeamDelete / SendMessage。

- TeamCreate / TeamDelete 是 lead 的顶层工具(建队改变 lead 运行模式,独立成工具可审计);
- SendMessage 是队员专属的横向通信工具(fire-and-forget,text 必带 summary);
- 双锁(配置 + 环境变量)通过时,TeamCreate 顺带进入 Coordinator Mode(§10):
  收窄 lead 工具集(剥写类)并把四阶段提示注入返回结果。
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from pydantic import BaseModel, Field

from archcode.teams.manager import TeamManager
from archcode.teams.models import MailboxMessage, TeamError
from archcode.teams.prompts import COORDINATOR_PROMPT, LEAD_COORDINATION_HINT
from archcode.tools.base import Tool, ToolResult


def coordinator_enabled(config: Any) -> bool:
    """双锁:配置开关 ∧ 环境变量显式 opt-in(§10.2,防不知情改变 lead 行为)。"""
    flag = bool(getattr(config, "coordinator_mode", False)) if config is not None else False
    env = os.environ.get("ARCHCODE_COORDINATOR_MODE", "").strip() not in ("", "0", "false")
    return flag and env


def _strip_write_tools(registry: Any) -> int:
    """Coordinator 收窄:移除 lead 注册表里的写类工具;返回移除数。"""
    removed = 0
    for name in ("WriteFile", "EditFile"):
        if registry.unregister(name):
            removed += 1
    return removed


class TeamCreateTool(Tool):
    """lead 专用:建团队(目录三件套 + lead 注册 + 同名加序号)。"""

    name = "TeamCreate"
    description = (
        "Create a team for multi-agent collaboration. You become the lead; "
        "spawn teammates with the Agent tool passing team_name."
    )
    category = "command"

    class Params(BaseModel):
        team_name: str = Field(description="团队名(经 slug 校验;同名自动加序号)")
        description: str = Field(default="", description="团队用途一句话")
        agent_type: str = Field(default="", description="全队默认队员类型(可选)")

    params_model = Params

    def __init__(self, manager: TeamManager, config: Any = None, registry: Any = None) -> None:
        self._manager = manager
        self._config = config
        self._tool_registry = registry  # Coordinator 收窄写类工具用(lead 的注册表)

    async def execute(self, params: BaseModel) -> ToolResult:
        p: TeamCreateTool.Params = params  # type: ignore[assignment]
        try:
            team = self._manager.create_team(
                p.team_name.strip(), description=p.description, default_agent_type=p.agent_type.strip()
            )
        except TeamError as e:
            return ToolResult(f"Error: {e}", is_error=True)
        output = (
            f"团队已建立: {team.name}\n"
            f"下一步: 用 Agent 工具(带 team_name=\"{team.name}\")派生队员;用共享任务工具拆解任务。\n"
            f"{LEAD_COORDINATION_HINT}"
        )
        if coordinator_enabled(self._config):
            removed = _strip_write_tools(self._tool_registry)
            if removed:
                output += f"\n{COORDINATOR_PROMPT}\n(已移除 {removed} 个写类工具)"
        return ToolResult(output=output)


class TeamDeleteTool(Tool):
    """lead 专用:删团队(防活埋校验 → 删副本由 lead 经各队员清理 → 清目录)。"""

    name = "TeamDelete"
    description = "Delete a team. Fails if any teammate is still active."
    category = "command"

    class Params(BaseModel):
        team_name: str

    params_model = Params

    def __init__(self, manager: TeamManager) -> None:
        self._manager = manager

    async def execute(self, params: BaseModel) -> ToolResult:
        p: TeamDeleteTool.Params = params  # type: ignore[assignment]
        try:
            await self._manager.delete_team(p.team_name.strip())
        except TeamError as e:
            return ToolResult(f"Error: {e}", is_error=True)
        return ToolResult(output=f"团队已删除: {p.team_name}")


class SendMessageTool(Tool):
    """队员专属:横向通信(fire-and-forget;text 必带 summary;"*" 广播)。

    目标已停止(is_active=False)时走唤醒续写(§8.2):transcript 恢复 + 追加指令,
    由构造时注入的 wake 回调执行(agent_tool 提供,闭包捕获 lead 上下文);
    不可达(无 transcript)由 wake 返回错误,不静默丢弃(§5.3 报错纪律)。
    """

    name = "SendMessage"
    description = (
        "Send a message to a teammate (to=name or agent id; to='*' broadcasts). "
        "Fire-and-forget: delivery to the teammate's next loop. A stopped teammate "
        "is woken with this message and resumes from its saved conversation."
    )
    category = "command"

    class Params(BaseModel):
        to: str = Field(description="收件人:队友名 / agent id / '*' 广播")
        message: str = Field(description="正文")
        summary: str = Field(description="5-10 词摘要(UI 预览)")
        message_type: str = Field(default="text", description="text | shutdown_request | shutdown_response")

    params_model = Params

    def __init__(
        self,
        manager: TeamManager,
        team_name: str,
        from_name: str,
        wake: Any = None,
    ) -> None:
        self._manager = manager
        self._team = team_name
        self._from = from_name
        # wake: async (team_name, Teammate, message) -> ToolResult | None;
        # None = 未接线(退化为纯投递)
        self._wake = wake

    async def execute(self, params: BaseModel) -> ToolResult:
        p: SendMessageTool.Params = params  # type: ignore[assignment]
        try:
            if p.to.strip() == "*":
                count = self._manager.broadcast(self._team, self._from, p.message, p.summary)
                return ToolResult(output=f"已广播给 {count} 名队员")
            member = self._manager.resolve_member(self._team, p.to.strip())
            # 已停止 → 唤醒续写(§8.2):跑过的队员有 transcript,恢复后追加指令;
            # 未开始(is_active=None)不唤醒——消息留邮箱,它启动时第一轮 drain 读到
            if member.is_active is False and self._wake is not None:
                return await self._wake(self._team, member, p.message)
            self._manager.deliver(
                self._team,
                MailboxMessage(
                    msg_id=uuid.uuid4().hex[:12],
                    from_name=self._from,
                    to_name=member.name,
                    message_type=p.message_type,
                    content=p.message,
                    summary=p.summary,
                ),
            )
        except TeamError as e:
            return ToolResult(f"Error: {e}", is_error=True)
        except ValueError as e:
            return ToolResult(f"Error: {e}", is_error=True)
        return ToolResult(output=f"已投递给 {member.name}(下一轮 Loop 开头送达)")


# ── 团队版共享任务四件套(队员专属;绑定团队 tasks.json,与 lead 版同名不同绑定 §5.1)──


class TeamTaskCreateTool(Tool):
    name = "TaskCreate"
    description = "Create a task in the team shared task list."
    category = "command"

    class Params(BaseModel):
        subject: str
        description: str = ""

    params_model = Params

    def __init__(self, manager: TeamManager, team_name: str) -> None:
        self._manager = manager
        self._team = team_name

    async def execute(self, params: BaseModel) -> ToolResult:
        p: TeamTaskCreateTool.Params = params  # type: ignore[assignment]
        task = self._manager.tasks(self._team).create(p.subject, p.description)
        return ToolResult(output=f"任务已创建: [{task.id}] {task.subject}")


class TeamTaskGetTool(Tool):
    name = "TaskGet"
    description = "Get one task from the team shared task list by id."
    category = "read"

    class Params(BaseModel):
        task_id: str

    params_model = Params

    def __init__(self, manager: TeamManager, team_name: str) -> None:
        self._manager = manager
        self._team = team_name

    async def execute(self, params: BaseModel) -> ToolResult:
        p: TeamTaskGetTool.Params = params  # type: ignore[assignment]
        t = self._manager.tasks(self._team).get(p.task_id)
        if t is None:
            return ToolResult(f"Error: 任务不存在: {p.task_id}", is_error=True)
        deps = f"; blocked_by={t.blocked_by}" if t.blocked_by else ""
        return ToolResult(output=f"[{t.id}] {t.subject} ({t.status}, owner={t.owner or '未认领'}){deps}\n{t.description}")


class TeamTaskListTool(Tool):
    name = "TaskList"
    description = "List all tasks in the team shared task list (with claimable status)."
    category = "read"

    class Params(BaseModel):
        pass

    params_model = Params

    def __init__(self, manager: TeamManager, team_name: str) -> None:
        self._manager = manager
        self._team = team_name

    async def execute(self, params: BaseModel) -> ToolResult:
        store = self._manager.tasks(self._team)
        tasks = store.list()
        if not tasks:
            return ToolResult(output="(共享任务列表为空)")
        lines = []
        for t in tasks:
            mark = "✓可认领" if store.claimable(t) else ""
            deps = f" 被{t.blocked_by}阻塞" if t.blocked_by else ""
            lines.append(f"[{t.id}] {t.subject} ({t.status}, owner={t.owner or '未认领'}){deps} {mark}")
        return ToolResult(output="\n".join(lines))


class TeamTaskUpdateTool(Tool):
    name = "TaskUpdate"
    description = (
        "Update a team task: status (pending|in_progress|completed), owner, "
        "or dependencies (add_blocks/add_blocked_by)."
    )
    category = "command"

    class Params(BaseModel):
        task_id: str
        status: str = Field(default="", description="pending | in_progress | completed")
        owner: str = Field(default="", description="认领人(队员名);传 'me' 解析为当前队员")
        add_blocks: list[str] = Field(default_factory=list)
        add_blocked_by: list[str] = Field(default_factory=list)

    params_model = Params

    def __init__(self, manager: TeamManager, team_name: str, teammate_name: str = "") -> None:
        self._manager = manager
        self._team = team_name
        self._me = teammate_name

    async def execute(self, params: BaseModel) -> ToolResult:
        p: TeamTaskUpdateTool.Params = params  # type: ignore[assignment]
        owner = self._me if p.owner == "me" and self._me else (p.owner or None)
        try:
            t = self._manager.tasks(self._team).update(
                p.task_id,
                status=p.status or None,
                owner=owner,
                add_blocks=p.add_blocks or None,
                add_blocked_by=p.add_blocked_by or None,
            )
        except ValueError as e:
            return ToolResult(f"Error: {e}", is_error=True)
        if t is None:
            return ToolResult(f"Error: 任务不存在: {p.task_id}", is_error=True)
        return ToolResult(output=f"已更新: [{t.id}] {t.subject} ({t.status}, owner={t.owner or '未认领'})")
