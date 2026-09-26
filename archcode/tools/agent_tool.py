"""AgentTool:统一子 agent 工具(sub-agent-design §2/§3/§5/§8)。

execute 流程(§3):caller is_fork 检查 → isolation stub 检查 → 按 subagent_type
分流(Fork / 定义式)→ selectLLM 三级优先 → 四道防线过滤注册表(§4.3)→
独立 checker(§10)→ 新建子 Agent → 前台 runToCompletion 或后台 launch(§8)。

主 Agent 调它与调 Bash 完全同构:自动继承 _execute_tool 治理链(§1)。
is_fork 内存标记 + caller 拦截是防递归兜底(§5.5)——主手段是四道防线在
工具层让 Agent 不可见。
"""

from __future__ import annotations

import copy
import logging
import time
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
from archcode.tools.base import Tool, ToolResult

log = logging.getLogger(__name__)

log = logging.getLogger(__name__)


class AgentToolParams(BaseModel):
    """创建子 agent 的传入参数(§2.1:选项放在调用侧)。"""

    prompt: str
    description: str
    subagent_type: str | None = None    # 留空 → Fork 路径(§5)
    model: str | None = None            # 调用时覆盖定义里的模型(§3.2)
    run_in_background: bool = False     # 同步 vs 异步(§8)
    name: str | None = None             # 实例显示名(TaskList / 通知展示,§8.6)
    isolation: str | None = None        # "worktree" → stub 报错(§7.2)


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
        task_manager: TaskManager,
        parent_agent: Any,
        llm_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._agent_loader = agent_loader
        self._task_manager = task_manager
        self._parent_agent = parent_agent
        self._llm_factory = llm_factory  # 按模型别名造子 client;失败/未配置 → 回落父

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

        # isolation:worktree 只落 stub,报错不降级(§7.2 拍板)
        if p.isolation not in (None, ""):
            if p.isolation == "worktree":
                log.info("sub-agent rejected: isolation=worktree 尚未实现(stub)")
                return ToolResult(
                    "Error: isolation='worktree' 尚未实现(worktree 模块为接口 stub,§7.2)。"
                    "请去掉 isolation 参数重试。",
                    is_error=True,
                )
            log.info("sub-agent rejected: unknown isolation %r", p.isolation)
            return ToolResult(f"Error: 未知 isolation 模式: {p.isolation!r}", is_error=True)

        fork_mode = not p.subagent_type
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

        # 独立 checker(§10):dontAsk=矩阵全放行;非 dontAsk 的 ask→拒绝
        mode = (
            PermissionMode.DONT_ASK
            if definition.permission_mode == "dontAsk"
            else PermissionMode(definition.permission_mode)
        )
        checker = PermissionChecker(
            mode=mode,
            sandbox=PathSandbox(parent._work_dir) if parent._work_dir else None,
        )
        if definition.permission_mode != "dontAsk":
            checker = _SubAgentChecker(checker)

        # 新建子 Agent(§5.1:两模式共用同一构造器,只字段值不同)
        sub_agent = AgentTool._create_agent(
            parent=parent,
            client=client,
            system_prompt=parent._system_prompt if fork_mode else definition.system_prompt,
            registry=registry,
            checker=checker,
            max_iterations=definition.max_turns,
            clone_replacement_state=fork_mode,  # §5.2:fork 克隆父状态保 cache 前缀
        )
        sub_agent.is_fork = fork_mode          # 防递归兜底标记(§5.5)
        sub_agent.parent_id = getattr(parent, "parent_id", None)

        runner = SubAgentRunner(sub_agent, conversation, inject_task=not fork_mode)

        display = p.name or p.subagent_type or "fork"
        log.info(
            "sub-agent spawned: type=%s mode=%s name=%s",
            p.subagent_type or "fork",
            "background" if is_background else "foreground",
            display,
        )

        if is_background:
            task_id = self._task_manager.launch(
                runner,
                task="" if fork_mode else p.prompt,
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
        result = await runner.run_to_completion(p.prompt)
        log.info(
            "sub-agent completed: type=%s elapsed=%.1fs",
            p.subagent_type,
            time.monotonic() - fg_start,
        )
        return ToolResult(output=result or "(sub-agent returned no output)")

    @staticmethod
    def _create_agent(
        parent: Any,
        client: Any,
        system_prompt: str,
        registry: Any,
        checker: Any,
        max_iterations: int,
        clone_replacement_state: bool,
    ) -> Any:
        from archcode.agent import Agent  # 延迟导入:避免模块级循环依赖

        sub = Agent(
            client=client,
            system_prompt=system_prompt,
            tool_registry=registry,
            permission_checker=checker,
            max_iterations=max_iterations,
            work_dir=parent._work_dir,
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
