"""AgentTool:统一子 agent 工具(sub-agent-design §2/§3/§5/§8;worktree-design §5)。

execute 流程(§3):caller is_fork 检查 → isolation 入口检查 → 按 subagent_type
分流(Fork / 定义式)→ selectLLM 三级优先 → 四道防线过滤注册表(§4.3)→
独立 checker(§10)→ 新建子 Agent → 前台 runToCompletion 或后台 launch(§8)。
isolation=worktree 时展开为七步工作流(worktree-design §5):创建副本 →
work_dir/sandbox 指向副本 → 注入上下文通知 → 运行 → auto_cleanup 验货 →
保留时结果追加路径/分支。

主 Agent 调它与调 Bash 完全同构:自动继承 _execute_tool 治理链(§1)。
is_fork 内存标记 + caller 拦截是防递归兜底(§5.5)——主手段是四道防线在
工具层让 Agent 不可见。
"""

from __future__ import annotations

import copy
import logging
import time
import uuid
from typing import Any, Callable

from pydantic import BaseModel

from archcode.agents.fork import ForkError, build_forked_messages
from archcode.agents.models import AgentDef
from archcode.agents.task_manager import SubAgentRunner, TaskManager
from archcode.agents.tool_filter import resolve_agent_tools
from archcode.conversation.manager import ConversationManager
from archcode.permissions import PermissionChecker, PermissionMode
from archcode.permissions.checker import Decision
from archcode.permissions.sandbox import PathSandbox
from archcode.teams.models import MailboxMessage, Teammate
from archcode.tools.base import Tool, ToolResult

log = logging.getLogger(__name__)


class AgentToolParams(BaseModel):
    """创建子 agent 的传入参数(§2.1:选项放在调用侧)。"""

    prompt: str
    description: str
    subagent_type: str | None = None    # 留空 → Fork 路径(§5)
    model: str | None = None            # 调用时覆盖定义里的模型(§3.2)
    run_in_background: bool = False     # 同步 vs 异步(§8)
    name: str | None = None             # 实例显示名(TaskList / 通知展示,§8.6)
    isolation: str | None = None        # "worktree" → 独立副本隔离(worktree-design §5)
    team_name: str | None = None        # 非空 = 入队 spawn(队员,恒后台;agent-teams-design §7.1)


class _TeammateChecker:
    """队员权限检查(§5.5 冒泡):ask 决策不静默拒——转结构化消息发 lead 邮箱,
    返回拒绝 + 指路文案(lead 批准后队员权限模式更新,队员重试)。"""

    def __init__(self, inner: Any, manager: Any, team_name: str, teammate_name: str) -> None:
        self._inner = inner
        self._manager = manager
        self._team = team_name
        self._me = teammate_name

    @property
    def mode(self) -> PermissionMode:
        return self._inner.mode

    @mode.setter
    def mode(self, value: PermissionMode) -> None:
        self._inner.mode = value

    @property
    def plan_file_path(self) -> str:
        return self._inner.plan_file_path

    @plan_file_path.setter
    def plan_file_path(self, value: str) -> None:
        self._inner.plan_file_path = value

    def check(
        self, tool_name: str, category: str, arguments: dict[str, Any] | None = None
    ) -> Decision:
        decision = self._inner.check(tool_name, category, arguments)
        if getattr(decision, "behavior", None) == "ask" or getattr(decision, "action", "") == "ask":
            try:
                self._manager.deliver(
                    self._team,
                    MailboxMessage(
                        msg_id=uuid.uuid4().hex[:12],
                        from_name=self._me,
                        to_name="lead",
                        message_type="permission_request",
                        content=f"队员 {self._me} 请求权限: {tool_name} {arguments or {}}",
                        summary=f"{self._me} 请求 {tool_name} 权限",
                    ),
                )
            except Exception:  # 消息失败不影响拒绝主路径
                pass
        return decision


class _TeammateRunner:
    """队员的后台包装:跑完(自认为做完)→ 标记空闲 + transcript 落盘 + 向 lead
    邮箱发 idle 通知(§8.1/§8.2)。崩溃也走 finally:transcript 照存(唤醒恢复的
    前提),idle 通知照发(§8.1 崩溃不饿死 lead)。"""

    def __init__(
        self,
        runner: Any,
        manager: Any,
        team_name: str,
        teammate_name: str,
        agent_id: str = "",
    ) -> None:
        self._runner = runner
        self._manager = manager
        self._team = team_name
        self._me = teammate_name
        self._agent_id = agent_id or teammate_name

    async def run_to_completion(self, task: str) -> str:
        try:
            result = await self._runner.run_to_completion(task)
        finally:
            self._manager.mark_teammate_state(self._team, self._me, is_active=False)
            self._save_transcript()
            try:
                self._manager.deliver(
                    self._team,
                    MailboxMessage(
                        msg_id=uuid.uuid4().hex[:12],
                        from_name=self._me,
                        to_name="lead",
                        message_type="text",
                        content=f"队员 {self._me} 已空闲(本轮工作完成)",
                        summary=f"{self._me} 空闲",
                    ),
                )
            except Exception:
                pass
        return result

    def _save_transcript(self) -> None:
        """§8.2:run 结束即检查点落盘(空闲/中止都存——唤醒恢复的数据源)。"""
        try:
            from archcode.teams.transcript import save_transcript

            conversation = getattr(self._runner, "_conversation", None)
            history = getattr(conversation, "history", None) if conversation else None
            if history:
                save_transcript(
                    self._manager.team_dir(self._team), self._agent_id, history
                )
        except Exception as e:  # 落盘失败不遮蔽 finally 主路径
            log.warning("transcript 落盘失败(%s): %s", self._me, e)


class _WorktreeBgRunner:
    """后台任务的 worktree 收尾包装(worktree-design §5 步骤 5/6)。

    TaskManager 只要求 `run_to_completion(task) -> str`——包装对象在跑完后
    auto_cleanup 验货,保留时把路径/分支追加进结果,主 agent 据此 review/合并。
    子 agent 崩溃则不收尾,孤儿由后台清理兜底(§6.2)。
    """

    def __init__(self, runner: Any, manager: Any, wt: Any) -> None:
        self._runner = runner
        self._manager = manager
        self._wt = wt

    async def run_to_completion(self, task: str) -> str:
        result = await self._runner.run_to_completion(task)
        cleanup = await self._manager.auto_cleanup(self._wt.name, self._wt.head_commit)
        if cleanup.get("kept"):
            result = (result or "") + (
                f"\n[Worktree 保留在 {cleanup['path']}，分支 {cleanup['branch']}]"
            )
        return result


class _SubAgentChecker:
    """§10:非 dontAsk 子 agent 的 ask 决策**直接转拒绝**——无 HITL,绝不挂起。"""

    def __init__(self, inner: PermissionChecker) -> None:
        self._inner = inner

    @property
    def mode(self) -> PermissionMode:
        return self._inner.mode

    @mode.setter
    def mode(self, value: PermissionMode) -> None:
        self._inner.mode = value

    @property
    def plan_file_path(self) -> str:
        return self._inner.plan_file_path

    @plan_file_path.setter
    def plan_file_path(self, value: str) -> None:
        self._inner.plan_file_path = value

    def check(
        self, tool_name: str, category: str, arguments: dict[str, Any] | None = None
    ) -> Decision:
        decision = self._inner.check(tool_name, category, arguments)
        if decision.effect == "ask":
            return Decision(
                effect="deny",
                reason="子 agent 不支持交互确认:该调用需要权限但无法询问用户(§10)",
            )
        return decision



class AgentTool(Tool):
    """统一子 agent 工具:一次性委派(星型)与团队队员(team_name 入队)共用入口。

    选用指引:一次性子任务直接 spawn;多角色长期协作 / 需要共享任务与横向通信时,
    先 TeamCreate 组队,再带 team_name 派队员(队员恒后台,空闲会通知)。
    """

    name = "Agent"
    description = (
        "Launch a sub-agent to handle a task in an isolated context. "
        "Use subagent_type to select a predefined agent type (e.g. Explore, Plan, "
        "general-purpose), or leave it empty to fork the current conversation. "
        "Fixed-role tasks with narrow tool needs → specify subagent_type; "
        "temporary tasks needing the current conversation context → leave it empty."
    )
    params_model = AgentToolParams
    category = "command"
    is_concurrency_safe = False

    def __init__(
        self,
        agent_loader: Any,
        task_manager: Any,
        parent_agent: Any,
        llm_factory: Callable[[str], Any] | None = None,
        worktree_manager: Any | None = None,
        team_manager: Any | None = None,
        teams_config: Any | None = None,
    ) -> None:
        self._agent_loader = agent_loader
        self._task_manager = task_manager
        self._parent_agent = parent_agent
        self._llm_factory = llm_factory  # 按模型别名造子 client;失败/未配置 → 回落父
        self._worktree_manager = worktree_manager  # worktree 隔离(worktree-design §5)
        self._team_manager = team_manager  # teams 建队/花名册/邮箱(agent-teams-design)
        self._teams_config = teams_config  # TeamsConfig(fork_teammate / coordinator_mode)

    async def execute(self, params: BaseModel) -> ToolResult:
        p: AgentToolParams = params  # type: ignore[assignment]
        parent = self._parent_agent

        # 防递归兜底(§5.5):caller 是 fork 产生的子 agent → 直接拒
        if getattr(parent, "is_fork", False):
            log.info("sub-agent rejected: nested fork (caller is_fork)")
            return ToolResult(
                "Error: fork 产生的子 agent 不能再创建子 agent(嵌套禁止,§5.5)。",
                is_error=True,
            )

        # isolation 入口检查:未知模式拒绝(worktree-design §5.3)
        if p.isolation not in (None, "") and p.isolation != "worktree":
            log.info("sub-agent rejected: unknown isolation %r", p.isolation)
            return ToolResult(f"Error: 未知 isolation 模式: {p.isolation!r}", is_error=True)

        # ── teams 身份检查(§4.3.4 矩阵:队员不能再组队/禁后台子 Agent)──────
        is_teammate_caller = getattr(parent, "_is_teammate", False)
        if p.team_name and is_teammate_caller:
            log.info("sub-agent rejected: teammate cannot expand team")
            return ToolResult(
                "Error: 队员不能扩编团队,请联系 lead 添加成员。", is_error=True
            )
        if is_teammate_caller and p.run_in_background:
            log.info("sub-agent rejected: in-process teammate cannot spawn background")
            return ToolResult(
                "Error: 进程内队员只能 spawn 同步子 Agent(禁后台,§4.3.2)。", is_error=True
            )

        # ── teams 入队(§7.1):team 校验 + fork 开关(默认关)────────────
        use_team = bool(p.team_name) and not is_teammate_caller
        team = None
        if use_team:
            if self._team_manager is None:
                return ToolResult("Error: teams 模块未装配(TeamManager 不可用)。", is_error=True)
            team = self._team_manager.get_team(p.team_name.strip())
            if team is None:
                return ToolResult(f"Error: 团队不存在: {p.team_name}(先用 TeamCreate 建队)", is_error=True)

        fork_mode = not p.subagent_type
        if use_team and fork_mode and not getattr(self._teams_config, "fork_teammate", False):
            # fork 开关默认关:留空 agent_type 落全队默认定义式(§7.1,防误触发大成本 fork)
            if not team.default_agent_type:
                return ToolResult(
                    "Error: 建队时未设置默认队员类型(agent_type),fork 队员未开放;"
                    "请显式指定 subagent_type。",
                    is_error=True,
                )
            p = p.model_copy(update={"subagent_type": team.default_agent_type})
            fork_mode = False
        parent_conv = getattr(parent, "_current_conversation", None)

        if fork_mode:
            # Fork 路径(§5):继承父完整对话,无条件后台
            if parent_conv is None:
                log.info("sub-agent rejected: no active conversation")
                return ToolResult(
                    "Error: cannot fork: no active conversation in parent agent.",
                    is_error=True,
                )
            try:
                conversation = build_forked_messages(parent_conv, p.prompt)
            except ForkError as exc:
                log.info("sub-agent rejected: fork error: %s", exc)
                return ToolResult(str(exc), is_error=True)
            definition = AgentDef(
                agent_type="fork",
                when_to_use="Forked from parent agent",
                system_prompt="",       # fork 继承父 system_prompt(§5.1)
                tools=[],
                disallowed_tools=[],
                model="inherit",
                max_turns=parent._max_iterations,
                permission_mode="dontAsk",  # fork 建议基调(§10)
                source="builtin",
            )
        else:
            # 定义式路径(§4):取定义 → 空白对话
            definition = self._agent_loader.get(p.subagent_type)
            if definition is None:
                available = ", ".join(n for n, _ in self._agent_loader.list_agents()) or "(无)"
                log.info("sub-agent rejected: unknown type %r", p.subagent_type)
                return ToolResult(
                    f"Unknown agent type: '{p.subagent_type}'. "
                    f"Available types: {available}.",
                    is_error=True,
                )
            conversation = ConversationManager()

        # isolation 生效值:调用参数优先,其次定义(worktree-design §5)
        effective_isolation = p.isolation or definition.isolation
        if fork_mode and effective_isolation:
            log.info("sub-agent rejected: fork + isolation 互斥")
            return ToolResult(
                "Error: fork 继承父对话(含父目录路径),与 worktree 隔离互斥;请二选一。",
                is_error=True,
            )
        use_worktree = effective_isolation == "worktree"
        if use_team:
            use_worktree = True  # 队员默认带隔离副本(§7.1 步骤 2;第一版不做无副本队员)
        if use_worktree and self._worktree_manager is None:
            return ToolResult(
                "Error: worktree 模块未装配(WorktreeManager 不可用)。", is_error=True
            )

        # 队员名(寻址标识;lead 分配,缺省自动生成)。
        # 名字进文件路径(收件箱目录)与 worktree 名,必须过与 worktree 同款的白名单
        # 校验——LLM 可控输入,不校验就是路径穿越(§5.4 安全面)
        name_raw = (p.name or "").strip()
        if name_raw:
            from archcode.worktree.slug import validate_slug

            name_err = validate_slug(name_raw)
            if name_err:
                log.info("sub-agent rejected: bad teammate name %r (%s)", name_raw, name_err)
                return ToolResult(f"Error: 队员名不合法: {name_err}", is_error=True)
        teammate_name = name_raw or f"teammate-{uuid.uuid4().hex[:4]}"

        # worktree 七步(§5):create → 通知注入前置;失败报错中止,不降级
        wt = None
        task_text = p.prompt
        if use_worktree:
            from archcode.worktree import WorktreeError, build_worktree_notice, generate_worktree_name

            try:
                wt_name = (
                    f"team-{p.team_name}+{teammate_name}" if use_team else generate_worktree_name()
                )
                wt = await self._worktree_manager.create(wt_name)
            except WorktreeError as e:
                log.info("sub-agent rejected: worktree create failed: %s", e)
                return ToolResult(f"Error: 创建 worktree 失败: {e}", is_error=True)
            task_text = build_worktree_notice(str(parent._work_dir), wt.path) + "\n\n" + p.prompt

        # selectLLM 三级优先(§3.2):params.model → definition.model → 父 client
        client = parent._client
        alias = p.model or (definition.model if definition.model not in ("", "inherit") else None)
        if alias and self._llm_factory is not None:
            sub_client = self._llm_factory(alias)  # 别名解析失败返回 None → 回落父
            if sub_client is not None:
                client = sub_client

        # 四道防线过滤(§4.3;fork 恒后台 → 也过白名单,§8.4)
        is_background = fork_mode or p.run_in_background or definition.background
        registry = resolve_agent_tools(parent._tool_registry, definition, is_background)

        # 队员恒后台:装机/注册/启动走 spawn 与唤醒共用的方法(§7.1/§8.2);
        # 此分支之后全是"一次性委派"(星型)路径
        if use_team:
            task_id = await self._launch_teammate(
                parent=parent,
                team_name=p.team_name,
                teammate_name=teammate_name,
                agent_id=p.name or p.subagent_type or "fork",
                definition=definition,
                conversation=conversation,
                wt=wt,
                fork_style=fork_mode,
                inject_task=not fork_mode,
                task="" if fork_mode else task_text,
                model_alias=p.model or "",
                client=client,
                resume=False,
            )
            return ToolResult(
                f"队员已加入团队 {p.team_name}。\n"
                f"队员名: {teammate_name}  Task ID: {task_id}\n"
                f"副本: {wt.path if wt is not None else '(无)'}\n"
                f"下一步: 用团队共享任务工具拆解任务(TaskCreate + add_blocked_by 标依赖);\n"
                f"队员空闲时会通知你;SendMessage(to=\"{teammate_name}\") 可追加指令(§8.2 续写)。",
            )

        # 独立 checker(§10):dontAsk=矩阵全放行;非 dontAsk 的 ask→拒绝
        # worktree 模式下 sandbox 圈定到副本目录(§5 步骤 2)
        sandbox_root = wt.path if wt is not None else parent._work_dir
        mode = (
            PermissionMode.DONT_ASK
            if definition.permission_mode == "dontAsk"
            else PermissionMode(definition.permission_mode)
        )
        checker = PermissionChecker(
            mode=mode,
            sandbox=PathSandbox(sandbox_root) if sandbox_root else None,
        )
        if definition.permission_mode != "dontAsk":
            checker = _SubAgentChecker(checker)

        # 新建子 Agent(§5.1:两模式共用同一构造器,只字段值不同)
        system_prompt = parent._system_prompt if fork_mode else definition.system_prompt
        sub_agent = AgentTool._create_agent(
            parent=parent,
            client=client,
            system_prompt=system_prompt,
            registry=registry,
            checker=checker,
            max_iterations=definition.max_turns,
            clone_replacement_state=fork_mode,  # §5.2:fork 克隆父状态保 cache 前缀
            work_dir=sandbox_root,
        )
        sub_agent.is_fork = fork_mode          # 防递归兜底标记(§5.5)
        sub_agent.parent_id = getattr(parent, "parent_id", None)
        runner = SubAgentRunner(sub_agent, conversation, inject_task=not fork_mode)

        display = p.name or p.subagent_type or "fork"
        log.info(
            "sub-agent spawned: type=%s mode=%s name=%s team=%s",
            p.subagent_type or "fork",
            "background" if is_background else "foreground",
            display,
            p.team_name or "-",
        )

        if is_background:
            bg_agent: Any = runner
            if wt is not None:
                bg_agent = _WorktreeBgRunner(runner, self._worktree_manager, wt)
            task_id = self._task_manager.launch(
                bg_agent,
                task="" if fork_mode else task_text,
                name=display,
            )
            return ToolResult(
                f"Sub-agent launched in background.\n"
                f"Task ID: {task_id}\n"
                f"Agent: {display}\n"
                f"Type: {'fork' if fork_mode else p.subagent_type}\n"
                f"The system will notify automatically when it completes.\n"
                f"Do NOT wait, sleep, or poll. Report the task ID to the user and move on.",
            )

        fg_start = time.monotonic()
        result = await runner.run_to_completion(task_text)
        log.info(
            "sub-agent completed: type=%s elapsed=%.1fs",
            p.subagent_type,
            time.monotonic() - fg_start,
        )
        # worktree 收尾(§5 步骤 5/6):auto_cleanup 验货,保留时追加路径/分支
        if wt is not None:
            cleanup = await self._worktree_manager.auto_cleanup(wt.name, wt.head_commit)
            if cleanup.get("kept"):
                result = (result or "") + (
                    f"\n[Worktree 保留在 {cleanup['path']}，分支 {cleanup['branch']}]"
                )
        return ToolResult(output=result or "(sub-agent returned no output)")

    async def _launch_teammate(
        self,
        parent: Any,
        team_name: str,
        teammate_name: str,
        agent_id: str,
        definition: Any,
        conversation: Any,
        wt: Any,
        fork_style: bool,
        inject_task: bool,
        task: str,
        model_alias: str,
        client: Any,
        resume: bool,
    ) -> str:
        """队员装机 + 注册 + 恒后台启动(spawn 与唤醒续写共用,§7.1/§8.2)。

        协调工具注入 / 冒泡 checker / 邮箱 drain 钩子 / 空闲登记都在这里;
        spawn 与 resume 只差 conversation 来源(空白或 fork vs transcript)、
        注册方式(进花名册 vs 翻活跃)与 client 来源。返回 Task ID。
        """
        from archcode.tools.team_tools import (
            SendMessageTool,
            TeamTaskCreateTool,
            TeamTaskGetTool,
            TeamTaskListTool,
            TeamTaskUpdateTool,
        )

        # 四道防线过滤(队员恒后台 → 过白名单,§4.3)
        registry = resolve_agent_tools(parent._tool_registry, definition, True)

        # 协调工具注入(过滤后追加,防被防线滤掉;§5.1)
        # wake 闭包捕获 spawn 时的 lead 上下文(parent)——恢复出的队员继承 lead
        # 的基础设施与 client,与初次 spawn 同源,不依赖"谁在调用 SendMessage"
        def _wake(team: str, member: Any, message: str) -> Any:
            return self._wake_teammate(parent, team, member, message)

        registry.register(
            SendMessageTool(self._team_manager, team_name, teammate_name, wake=_wake),
            owner=f"team:{team_name}",
        )
        for tool_cls in (TeamTaskCreateTool, TeamTaskGetTool, TeamTaskListTool, TeamTaskUpdateTool):
            if tool_cls is TeamTaskUpdateTool:
                registry.register(
                    tool_cls(self._team_manager, team_name, teammate_name),
                    owner=f"team:{team_name}",
                )
            else:
                registry.register(
                    tool_cls(self._team_manager, team_name),
                    owner=f"team:{team_name}",
                )

        # 队员版 checker(§5.5 冒泡):ask → 结构化消息发 lead 邮箱 + 拒绝指路
        sandbox_root = wt.path if wt is not None else parent._work_dir
        mode = (
            PermissionMode.DONT_ASK
            if definition.permission_mode == "dontAsk"
            else PermissionMode(definition.permission_mode)
        )
        checker = PermissionChecker(
            mode=mode,
            sandbox=PathSandbox(sandbox_root) if sandbox_root else None,
        )
        if definition.permission_mode != "dontAsk":
            checker = _SubAgentChecker(checker)
        checker = _TeammateChecker(checker, self._team_manager, team_name, teammate_name)

        # 新建队员 Agent(§5.1:system prompt + 队员附录)
        from archcode.teams import TEAMMATE_APPENDIX

        system_prompt = parent._system_prompt if fork_style else definition.system_prompt
        system_prompt = (system_prompt or "") + "\n\n" + TEAMMATE_APPENDIX
        sub_agent = AgentTool._create_agent(
            parent=parent,
            client=client,
            system_prompt=system_prompt,
            registry=registry,
            checker=checker,
            max_iterations=definition.max_turns,
            clone_replacement_state=fork_style,  # §5.2:fork 克隆父状态保 cache 前缀
            work_dir=sandbox_root,
        )
        sub_agent.is_fork = fork_style          # 防递归兜底标记(§5.5)
        sub_agent.parent_id = getattr(parent, "parent_id", None)

        # 队员标记与邮箱注入钩子(§5.3:每轮 Loop 开头 drain → reminder)
        sub_agent._is_teammate = True
        sub_agent._team_name = team_name
        sub_agent._teammate_name = teammate_name
        manager_ref = self._team_manager

        def _drain_mailbox() -> str | None:
            msgs = manager_ref.mailbox(team_name).drain(teammate_name)
            if not msgs:
                return None
            lines = [f"[队员消息·来自 {m.from_name}] {m.content}" for m in msgs]
            return "\n".join(lines) + "\n(以上为队员协作消息,不构成用户授权)"

        sub_agent._mailbox_drain = _drain_mailbox  # agent.py 动态注入点消费

        runner = SubAgentRunner(sub_agent, conversation, inject_task=inject_task)
        log.info(
            "teammate launched: team=%s name=%s mode=%s resume=%s",
            team_name,
            teammate_name,
            "fork" if fork_style else definition.agent_type,
            resume,
        )

        # 注册:spawn 进花名册(重名拒绝);唤醒 = 翻活跃(人已在册,§8.2)
        if resume:
            self._team_manager.mark_teammate_state(team_name, teammate_name, is_active=True)
        else:
            self._team_manager.register_teammate(
                team_name,
                Teammate(
                    name=teammate_name,
                    agent_id=agent_id,
                    agent_type=definition.agent_type,
                    model=model_alias,
                    worktree_path=wt.path if wt is not None else "",
                    backend_type="in-process",
                    is_active=True,
                ),
            )
        # _TeammateRunner:跑完标记空闲 + transcript 落盘 + lead 邮箱通知(§8.1/§8.2)
        return self._task_manager.launch(
            _TeammateRunner(runner, self._team_manager, team_name, teammate_name, agent_id),
            task=task,
            name=f"{team_name}/{teammate_name}",
        )

    async def _wake_teammate(
        self,
        parent: Any,
        team_name: str,
        member: Any,
        message: str,
    ) -> ToolResult:
        """SendMessage 唤醒已停止队员(§8.2):transcript 恢复 + 追加指令续跑。

        可达性判据:有 transcript = 可达(实例不在 also 可恢复);无 transcript
        (从未成功启动 / 记录被清)→ 报"目标不可达",不静默丢弃(§5.3)。
        寻址靠 name 不靠 id:恢复后实例 agent_id 沿用花名册里的值(transcript 键)。
        """
        from archcode.teams.transcript import load_transcript
        from archcode.worktree import WorktreeError

        history = load_transcript(self._team_manager.team_dir(team_name), member.agent_id)
        if history is None:
            return ToolResult(
                f"Error: 目标不可达:{member.name} 无对话记录可恢复"
                f"(从未成功启动或记录已被清理)",
                is_error=True,
            )

        definition = self._agent_loader.get(member.agent_type)
        fork_style = definition is None
        if fork_style:
            # fork 型队员(agent_type="fork" 无定义文件):回落父身份,与 spawn 的
            # fork 路径同构(system prompt 继承 lead)
            definition = AgentDef(
                agent_type="fork",
                when_to_use="resumed teammate",
                system_prompt="",
                tools=[],
                disallowed_tools=[],
                model="inherit",
                max_turns=getattr(parent, "_max_iterations", 50),
                permission_mode="dontAsk",
                source="builtin",
            )

        conversation = ConversationManager()
        conversation.history.extend(history)

        # worktree 复用:副本在(账本/磁盘)→ 直接用;被清了 → 按同名重建(§7.1 步骤 2)
        wt = None
        if self._worktree_manager is not None:
            try:
                wt = await self._worktree_manager.ensure(f"team-{team_name}+{member.name}")
            except WorktreeError as e:
                return ToolResult(f"Error: 唤醒失败(worktree): {e}", is_error=True)

        # client:队员定义/花名册里的 model 别名优先,解析失败回落 lead
        client = parent._client
        if member.model and self._llm_factory is not None:
            sub_client = self._llm_factory(member.model)
            if sub_client is not None:
                client = sub_client

        task_id = await self._launch_teammate(
            parent=parent,
            team_name=team_name,
            teammate_name=member.name,
            agent_id=member.agent_id,
            definition=definition,
            conversation=conversation,
            wt=wt,
            fork_style=fork_style,
            inject_task=True,  # 追加指令作为新 user 消息进恢复后的对话
            task=message,
            model_alias=member.model or "",
            client=client,
            resume=True,
        )
        return ToolResult(
            f"已唤醒 {member.name}(恢复对话 {len(history)} 条)并追加指令。\n"
            f"Task ID: {task_id}\n"
            f"副本: {wt.path if wt is not None else '(无)'}\n"
            f"队员完成会再次通知你。",
        )

    @staticmethod
    def _create_agent(
        parent: Any,
        client: Any,
        system_prompt: str,
        registry: Any,
        checker: Any,
        max_iterations: int,
        clone_replacement_state: bool,
        work_dir: Any = None,
    ) -> Any:
        from archcode.agent import Agent  # 延迟导入:避免模块级循环依赖

        sub = Agent(
            client=client,
            system_prompt=system_prompt,
            tool_registry=registry,
            permission_checker=checker,
            max_iterations=max_iterations,
            work_dir=work_dir if work_dir is not None else parent._work_dir,
        )
        # 基础设施共享(§6.2):hook 引擎与 skill 目录复用父的
        sub._hook_engine = parent._hook_engine
        sub._skill_loader = parent._skill_loader
        sub._spawned = True  # 日志 who 列:子 agent 的主线行标 "sub"
        # fork 克隆父的替换状态(§5.2):父子共享 tool_use_id 决策一致,
        # 保住共享 prompt-cache 前缀字节级一致(context/Layer1 决策冻结)
        if clone_replacement_state:
            sub._replacement_state = copy.deepcopy(parent._replacement_state)
        return sub
