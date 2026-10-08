"""TeamManager(agent-teams-design §4/§6):团队的数据层管理。

职责:团队目录三件套(config/tasks/mailbox)的创建与清理、花名册登记、
消息投递、空闲登记。队员的**执行**(构建 Agent 实例、worktree、TaskManager
协程、唤醒恢复)由调用侧(agent_tool)完成——本类只管数据与投递,不碰 Agent。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from archcode.teams.mailbox import TeamMailbox
from archcode.teams.models import MailboxMessage, Team, TeamError, Teammate
from archcode.teams.registry import AgentNameRegistry
from archcode.teams.shared_task import SharedTaskStore

logger = logging.getLogger(__name__)


class TeamManager:
    """per work_dir 单例(装配层构造)。teams_dir 由 paths.teams_dir 提供。"""

    def __init__(
        self,
        teams_dir: str | Path,
        lead_agent_id: str = "lead",
        worktree_manager: Any = None,
    ) -> None:
        self._base = Path(teams_dir)
        self._lead_agent_id = lead_agent_id
        self.registry = AgentNameRegistry()
        # 删队动作序第②步用(§4.5/§6.5):清队员 worktree 副本;None = 未装配(测试/降级)
        self._worktree_manager = worktree_manager

    # ------------------------------------------------------------------
    # 目录与配置
    # ------------------------------------------------------------------

    def team_dir(self, name: str) -> Path:
        return self._base / name

    def _config_path(self, name: str) -> Path:
        return self.team_dir(name) / "config.json"

    def _load(self, name: str) -> Team | None:
        p = self._config_path(name)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("team config 读取失败: %s (%s)", name, e)
            return None
        return Team(
            name=str(data.get("name", name)),
            lead_agent_id=str(data.get("lead_agent_id", "")),
            description=str(data.get("description", "")),
            default_agent_type=str(data.get("default_agent_type", "")),
            config_path=str(p),
            members=[
                Teammate(
                    name=str(m.get("name", "")),
                    agent_id=str(m.get("agent_id", "")),
                    agent_type=str(m.get("agent_type", "")),
                    model=str(m.get("model", "")),
                    worktree_path=str(m.get("worktree_path", "")),
                    backend_type=m.get("backend_type", "in-process"),
                    is_active=m.get("is_active"),
                    plan_mode_required=bool(m.get("plan_mode_required", False)),
                )
                for m in data.get("members", [])
            ],
        )

    def _save(self, team: Team) -> None:
        p = Path(team.config_path) if team.config_path else self._config_path(team.name)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "name": team.name,
                "lead_agent_id": team.lead_agent_id,
                "description": team.description,
                "default_agent_type": team.default_agent_type,
                "members": [
                    {
                        "name": m.name,
                        "agent_id": m.agent_id,
                        "agent_type": m.agent_type,
                        "model": m.model,
                        "worktree_path": m.worktree_path,
                        "backend_type": m.backend_type,
                        "is_active": m.is_active,
                        "plan_mode_required": m.plan_mode_required,
                    }
                    for m in team.members
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        tmp = p.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(p)

    # ------------------------------------------------------------------
    # 建队 / 删队(§6.1/§6.5)
    # ------------------------------------------------------------------

    def create_team(self, name: str, description: str = "", default_agent_type: str = "") -> Team:
        """建队;同名自动追加序号(§4.5)。目录三件套就位 + lead 注册。"""
        final = name
        while self._config_path(final).exists():
            m = re.search(r"-(\d+)$", final)
            final = f"{final}-{int(m.group(1)) + 1}" if m else f"{final}-2"

        team = Team(
            name=final,
            lead_agent_id=self._lead_agent_id,
            description=description,
            default_agent_type=default_agent_type,
            config_path=str(self._config_path(final)),
        )
        team.members.append(
            Teammate(name="lead", agent_id=self._lead_agent_id, agent_type="lead", is_active=True)
        )
        self._save(team)
        self.tasks(final)  # 初始化 tasks.json(空表落盘)
        TeamMailbox(self.team_dir(final))  # mailbox 目录惰性创建,此处仅确保 team 目录存在
        logger.info("team created: %s (dir=%s)", final, self.team_dir(final))
        return team

    async def delete_team(self, name: str) -> None:
        """删队(§4.5 动作序):①校验全体队员空闲(防活埋)→ ②删各队员 worktree
        (含分支清理)→ ③清邮箱与团队目录。唯一留存:合并进主分支的代码与 git 历史。"""
        import shutil

        team = self._load(name)
        if team is None:
            raise TeamError(f"not found: {name}")
        busy = [m.name for m in team.members if m.name != "lead" and m.is_active is not False]
        if busy:
            raise TeamError(f"队员仍在活跃中: {', '.join(busy)}——等待空闲或先停止")
        # ② 清队员副本(先取路径再清花名册;成员副本命名 team-<队>+<人>,非临时模式,
        # 后台五层漏斗不会收——删队是它们唯一的回收点)
        wt_paths = [m.worktree_path for m in team.members if m.worktree_path]
        if wt_paths and self._worktree_manager is not None:
            for path in wt_paths:
                try:
                    await self._worktree_manager.remove_path(path)
                except Exception as e:  # 单条失败不阻断删队(漏斗+人工可兜底)
                    logger.warning("队员副本删除失败(跳过): %s (%s)", path, e)
        for m in team.members:
            self.registry.unregister(m.name)
        shutil.rmtree(self.team_dir(name), ignore_errors=True)
        logger.info("team deleted: %s", name)

    def get_team(self, name: str) -> Team | None:
        return self._load(name)

    def list_teams(self) -> list[str]:
        if not self._base.exists():
            return []
        return sorted(p.name for p in self._base.iterdir() if (p / "config.json").exists())

    def tasks(self, name: str) -> SharedTaskStore:
        return SharedTaskStore(self.team_dir(name))

    def mailbox(self, name: str) -> TeamMailbox:
        return TeamMailbox(self.team_dir(name))

    # ------------------------------------------------------------------
    # 花名册与消息投递
    # ------------------------------------------------------------------

    def register_teammate(self, name: str, teammate: Teammate) -> Team:
        """spawn 六步的第 6 步:队员登记进花名册 + 名称注册表。"""
        team = self._load(name)
        if team is None:
            raise TeamError(f"not found: {name}")
        if team.teammate_by_name(teammate.name) is not None:
            raise TeamError(f"队员名已存在: {teammate.name}")
        team.members.append(teammate)
        self._save(team)
        self.registry.register(teammate.name, teammate.agent_id)
        return team

    def mark_teammate_state(self, team_name: str, teammate_name: str, is_active: bool) -> None:
        """队员忙闲标记(投影的落盘面;恢复/重启时由任务表重算,§4.4)。"""
        team = self._load(team_name)
        if team is None:
            return
        m = team.teammate_by_name(teammate_name)
        if m is None:
            return
        m.is_active = is_active
        self._save(team)

    def resolve_member(self, team_name: str, to: str) -> Teammate:
        """寻址:队员名或 agent_id → Teammate;解析不到报错(不静默丢弃,§5.3)。"""
        team = self._load(team_name)
        if team is None:
            raise TeamError(f"not found: {team_name}")
        if to == "*":
            raise TeamError("广播请逐个投递(send 广播由工具层展开)")
        m = team.teammate_by_name(to)
        if m is not None:
            return m
        m = next((x for x in team.members if x.agent_id == to), None)
        if m is None:
            raise TeamError(f"找不到收件人: {to}")
        return m

    def deliver(self, team_name: str, msg: MailboxMessage) -> None:
        """投递一条消息(寻址已由调用方完成;fire-and-forget)。"""
        team = self._load(team_name)
        if team is None:
            raise TeamError(f"not found: {team_name}")
        self.mailbox(team_name).write(msg)

    def broadcast(self, team_name: str, from_name: str, content: str, summary: str) -> int:
        """广播:发给除发送者外的全部队员;返回投递数。"""
        team = self._load(team_name)
        if team is None:
            raise TeamError(f"not found: {team_name}")
        count = 0
        for m in team.members:
            if m.name == from_name or m.name == "lead":
                continue
            self.mailbox(team_name).write(
                MailboxMessage(
                    msg_id=uuid.uuid4().hex[:12],
                    from_name=from_name,
                    to_name=m.name,
                    content=content,
                    summary=summary,
                )
            )
            count += 1
        return count
