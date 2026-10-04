"""运行时装配:按工作目录组装一整套可运行的 agent 运行时。

抽取自 __main__ 的三分支公共装配序列(registry → ToolSearch → agent →
hooks → skills → agents 接线 → MCP 连接)。三分支与 webui 的工作区切换
共用同一份装配代码,消除手抄漂移(web 分支漏 _wire_agents 的教训)。
"""

from __future__ import annotations

import sys
from pathlib import Path

from archcode.agent import Agent
from archcode.hooks import HookEngine
from archcode.llm.client import create_client
from archcode.mcp import MCPManager
from archcode.memory import InstructionDocumentLoader
from archcode.permissions import PathSandbox, PermissionChecker, PermissionMode
from archcode.prompts import build_system_prompt
from archcode.skills import SkillExecutor, SkillLoader
from archcode.tools import create_default_registry
from archcode.tools.tool_search import ToolSearchTool


def build_tool_registry(work_dir: Path, protocol: str):
    """默认工具注册中心 + ToolSearch(延迟工具搜索入口)。"""
    registry = create_default_registry(work_dir=work_dir)
    registry.register(ToolSearchTool(registry, protocol=protocol))
    return registry


def _wire_hooks(config, work_dir: Path, agent: Agent) -> None:
    """创建 HookEngine 并接线(hooks-design §8)。诊断打 stderr。"""
    engine, diagnostics = HookEngine.from_config(
        config.hooks, work_dir=work_dir,
    )
    agent._hook_engine = engine
    for diagnostic in diagnostics:
        print(diagnostic, file=sys.stderr)


def _wire_skills(agent: Agent, tool_registry, work_dir: Path) -> SkillExecutor:
    """创建 SkillLoader / SkillExecutor 并接线(skills-design.md 7)。

    - loader 扫描三层目录,诊断打 stderr(遮蔽/解析失败);
    - LoadSkill 工具经 set_executor 接线(注册发生在 create_default_registry);
    - agent._skill_loader 供 Task 边界刷新 catalog。
    """
    loader = SkillLoader(work_dir=work_dir)
    executor = SkillExecutor(
        agent=agent,
        loader=loader,
        recovery=getattr(agent, "_recovery_state", None),
    )
    agent._skill_loader = loader
    tool = tool_registry.get("LoadSkill")
    if tool is not None and hasattr(tool, "set_executor"):
        tool.set_executor(executor)
    for diagnostic in loader.diagnostics:
        print(diagnostic, file=sys.stderr)
    return executor


def _wire_agents(config, agent: Agent, tool_registry, work_dir: Path, skill_executor=None) -> None:
    """创建 AgentLoader / TaskManager / AgentTool 并接线(sub-agent-design §11/§13)。

    - loader 扫描三层 agent 定义,诊断打 stderr;
    - AgentTool / TaskList / TaskGet 注册进主注册表;
    - agent._background_notifier 接后台通知 drain(§9.2),agent._agent_loader 供
      Task 边界刷新 <agent-catalog>(§2.5);
    - skill_executor.task_manager 供 skill fork 后台启动(§13);
    - WorktreeManager 单例 + restore_session(worktree-design §4/§2.7)。
    """
    from archcode.agents.loader import AgentLoader
    from archcode.agents.notification import make_background_notifier
    from archcode.agents.task_manager import TaskManager
    from archcode.tools.agent_tool import AgentTool
    from archcode.tools.task_tools import register_task_tools
    from archcode.worktree import WorktreeManager

    loader = AgentLoader(work_dir=work_dir)
    loader.load_all()
    for diagnostic in loader.diagnostics:
        print(diagnostic, file=sys.stderr)

    task_manager = TaskManager()
    # worktree 隔离:per work_dir 单例,启动时捡回未结束的会话(§2.7);
    # 后台清理任务由事件循环就绪处惰性启动(manager.ensure_cleanup_task)
    worktree_manager = WorktreeManager(work_dir)
    worktree_manager.restore_session()
    worktree_config = getattr(config, "worktree", None)
    if worktree_config is not None:
        worktree_manager._cleanup_interval = worktree_config.stale_cleanup_interval
        worktree_manager._cleanup_cutoff = worktree_config.stale_cutoff_hours
    agent._worktree_manager = worktree_manager

    agent_tool = AgentTool(
        agent_loader=loader,
        task_manager=task_manager,
        parent_agent=agent,
        worktree_manager=worktree_manager,
    )
    tool_registry.register(agent_tool)
    register_task_tools(tool_registry, task_manager)

    agent._agent_loader = loader
    agent._background_notifier = make_background_notifier(task_manager)
    agent._task_manager = task_manager  # Web 端任务面板的只读数据源
    if skill_executor is not None:
        skill_executor.task_manager = task_manager


def build_agent_sync(config, work_dir: Path, tool_registry) -> Agent:
    """同步构建 agent(不开新 event loop)。"""
    provider = config.providers[0]
    sandbox = PathSandbox(project_root=str(work_dir))
    permission_checker = PermissionChecker(
        sandbox=sandbox,
        mode=PermissionMode.DEFAULT,
    )
    system_prompt = build_system_prompt(
        work_dir=str(work_dir),
        extra=config.system_prompt,
    )
    return Agent(
        client=create_client(provider),
        system_prompt=system_prompt,
        tool_registry=tool_registry,
        permission_checker=permission_checker,
        max_output_tokens=provider.max_output_tokens,
        work_dir=work_dir,
        compression=config.compression,
        instruction_loader=InstructionDocumentLoader(),
    )


def wire_all(config, work_dir: Path, agent: Agent, tool_registry) -> SkillExecutor:
    """hooks + skills + agents 三段接线(顺序与既有分支一致)。"""
    _wire_hooks(config, work_dir, agent)
    skill_executor = _wire_skills(agent, tool_registry, work_dir)
    _wire_agents(config, agent, tool_registry, work_dir, skill_executor)
    return skill_executor


def build_agent_runtime(config, work_dir: Path, protocol: str):
    """一键装配:registry + agent + 全部接线。

    Returns: (tool_registry, agent, skill_executor)
    """
    tool_registry = build_tool_registry(work_dir, protocol)
    agent = build_agent_sync(config, work_dir, tool_registry)
    skill_executor = wire_all(config, work_dir, agent, tool_registry)
    return tool_registry, agent, skill_executor


async def connect_mcp(mcp_server_configs: list | None, tool_registry):
    """按配置连接 MCP server 并注册工具(配置为空时不建 manager)。

    Returns: (mcp_manager | None, errors, successes)
    """
    mcp_manager: MCPManager | None = None
    mcp_errors: list[str] = []
    mcp_successes: list[tuple[str, int]] = []
    if mcp_server_configs:
        mcp_manager = MCPManager()
        mcp_manager.load_configs(mcp_server_configs)
        try:
            mcp_errors, mcp_successes = await mcp_manager.register_all_tools(
                tool_registry
            )
        except Exception as e:
            print(f"[MCP init error] {e}", file=sys.stderr)
    return mcp_manager, mcp_errors, mcp_successes
