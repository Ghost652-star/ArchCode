from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from archcode.agent import Agent
from archcode.conversation.manager import ConversationManager
from archcode.llm.client import AuthenticationError, LLMError, create_client
from archcode.config import ConfigError, load_config
from archcode.mcp import MCPManager
from archcode.memory import (
    InstructionDocumentLoader,
    SessionManager,
    format_instruction_diagnostics,
)
from archcode.hooks import HookEngine
from archcode.permissions import PermissionChecker, PermissionMode, PathSandbox
from archcode.paths import debug_log_path, project_data_dir
from archcode.prompts import build_system_prompt
from archcode.skills import SkillExecutor, SkillLoader
from archcode.tools import create_default_registry
from archcode.tools.tool_search import ToolSearchTool


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


def _wire_agents(agent: Agent, tool_registry, work_dir: Path, skill_executor=None) -> None:
    """创建 AgentLoader / TaskManager / AgentTool 并接线(sub-agent-design §11/§13)。

    - loader 扫描三层 agent 定义,诊断打 stderr;
    - AgentTool / TaskList / TaskGet 注册进主注册表;
    - agent._background_notifier 接后台通知 drain(§9.2),agent._agent_loader 供
      Task 边界刷新 <agent-catalog>(§2.5);
    - skill_executor.task_manager 供 skill fork 后台启动(§13)。
    """
    from archcode.agents.loader import AgentLoader
    from archcode.agents.notification import make_background_notifier
    from archcode.agents.task_manager import TaskManager
    from archcode.tools.agent_tool import AgentTool
    from archcode.tools.task_tools import register_task_tools

    loader = AgentLoader(work_dir=work_dir)
    loader.load_all()
    for diagnostic in loader.diagnostics:
        print(diagnostic, file=sys.stderr)

    task_manager = TaskManager()
    agent_tool = AgentTool(
        agent_loader=loader, task_manager=task_manager, parent_agent=agent
    )
    tool_registry.register(agent_tool)
    register_task_tools(tool_registry, task_manager)

    agent._agent_loader = loader
    agent._background_notifier = make_background_notifier(task_manager)
    if skill_executor is not None:
        skill_executor.task_manager = task_manager


async def _build_runtime(config, work_dir, protocol):
    """异步初始化:建默认 registry + 注册 ToolSearch + 连 MCP server。

    Returns: (tool_registry, mcp_manager_or_None, mcp_errors, mcp_successes)
    """
    tool_registry = create_default_registry(work_dir=work_dir)
    tool_registry.register(ToolSearchTool(tool_registry, protocol=protocol))

    mcp_manager: MCPManager | None = None
    mcp_errors: list[str] = []
    mcp_successes: list[tuple[str, int]] = []
    if config.mcp_servers:
        mcp_manager = MCPManager()
        mcp_manager.load_configs(config.mcp_servers)
        try:
            mcp_errors, mcp_successes = await mcp_manager.register_all_tools(
                tool_registry
            )
        except Exception as e:
            print(f"[MCP init error] {e}", file=sys.stderr)

    return tool_registry, mcp_manager, mcp_errors, mcp_successes


async def _run_prompt(
    agent: Agent, prompt: str, mcp_manager: MCPManager | None, work_dir: Path
) -> None:
    conversation = ConversationManager()
    session = SessionManager(work_dir).create()
    session.bind(conversation)
    try:
        result = await agent.run_to_completion(prompt, conversation)
        for message in format_instruction_diagnostics(agent.last_instruction_diagnostics):
            print(message, file=sys.stderr)
        print(result, flush=True)
    finally:
        session.close()
        if mcp_manager is not None:
            await mcp_manager.shutdown()


def _build_agent_sync(config, work_dir, tool_registry):
    """TUI 路径的同步构建 agent(不开新 event loop)。"""
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


def _setup_logging(work_dir: Path) -> None:
    """日志最小集(deferred-designs #4):INFO 起步,写项目级 debug.log,追加模式。

    存量三处 getLogger(compactor / mcp.client / mcp.manager)与 hooks 引擎自动接入。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        filename=str(debug_log_path(work_dir)),
        filemode="a",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="archcode",
        description="ArchCode AI coding assistant",
    )
    parser.add_argument(
        "-p",
        metavar="PROMPT",
        default=None,
        help="Run non-interactively: send one prompt and print the reply",
    )
    parser.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        default=None,
        help="Path to config.yaml (overrides default search paths)",
    )
    parser.add_argument(
        "-w", "--work-dir",
        metavar="PATH",
        default=None,
        help=(
            "项目工作目录。工具读写的相对路径基准,plan 文件落盘位置。"
            "默认是启动 agent 时的当前目录。"
        ),
    )
    parser.add_argument(
        "--web",
        action="store_true",
        default=False,
        help="以 Web 界面启动(FastAPI 服务 + 浏览器)代替 TUI",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Web 模式服务端口(默认 8000)",
    )
    args = parser.parse_args()

    work_dir = Path(args.work_dir).resolve() if args.work_dir else Path(os.getcwd())
    project_data_dir(work_dir).mkdir(parents=True, exist_ok=True)
    _setup_logging(work_dir)

    try:
        config_path = Path(args.config) if args.config else None
        config = load_config(config_path, project_dir=work_dir)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.p is not None:
            # -p 路径:build + run + shutdown 全部在同一个 asyncio.run 里
            # 这样 MCP stdio_client 的 task group enter/exit 在同一个 task
            async def _oneshot():
                tool_registry, mcp_manager, mcp_errors, mcp_successes = (
                    await _build_runtime(config, work_dir, config.providers[0].protocol)
                )
                for name, count in mcp_successes:
                    print(f"[MCP] ✓ {name}: {count} tool(s) registered", file=sys.stderr)
                for err in mcp_errors:
                    print(f"[MCP] ✗ {err}", file=sys.stderr)
                agent = _build_agent_sync(config, work_dir, tool_registry)
                _wire_hooks(config, work_dir, agent)
                skill_executor = _wire_skills(agent, tool_registry, work_dir)
                _wire_agents(agent, tool_registry, work_dir, skill_executor)
                await _run_prompt(agent, args.p, mcp_manager, work_dir)

            asyncio.run(_oneshot())
        elif args.web:
            # Web 路径:装配同 TUI(一套 _build_agent_sync + wiring),MCP 在
            # FastAPI startup 里连(同 uvicorn loop,镜像 app.on_mount 模式)。
            from archcode.webui.server import run_web

            provider = config.providers[0]
            tool_registry = create_default_registry(work_dir=work_dir)
            tool_registry.register(
                ToolSearchTool(tool_registry, protocol=provider.protocol)
            )
            agent = _build_agent_sync(config, work_dir, tool_registry)
            _wire_hooks(config, work_dir, agent)
            _wire_skills(agent, tool_registry, work_dir)
            run_web(agent, work_dir, args.port, config.mcp_servers)
        else:
            # TUI 路径:build 同步做(create_default_registry 不需要 await),
            # MCP 连接放到 background task,在 TUI 的 event loop 里跑。
            # 这样 stdio_client 的 task group 跟 TUI 是同一个 event loop。
            from archcode.app import ArchCodeApp
            from archcode.driver import NoAltScreenDriver

            provider = config.providers[0]
            tool_registry = create_default_registry(work_dir=work_dir)
            tool_registry.register(
                ToolSearchTool(tool_registry, protocol=provider.protocol)
            )

            agent = _build_agent_sync(config, work_dir, tool_registry)
            _wire_hooks(config, work_dir, agent)
            skill_executor = _wire_skills(agent, tool_registry, work_dir)
            _wire_agents(agent, tool_registry, work_dir, skill_executor)

            app = ArchCodeApp(
                agent=agent,
                model_name=provider.model,
                driver_class=NoAltScreenDriver,
                skill_executor=skill_executor,
            )
            # 把 mcp_servers 配置传给 app,它在 on_mount 里 background task 启动
            app._mcp_server_configs = config.mcp_servers
            app.run()
            # MCP 清理交给 app.on_unmount(跟 TUI 同一个 event loop,避免跨 loop 死锁)
    except LLMError as e:
        print(f"LLM error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
