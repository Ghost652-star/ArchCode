from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from archcode.agent import Agent
from archcode.conversation.manager import ConversationManager
from archcode.llm.client import AuthenticationError, LLMError, create_client
from archcode.config import ConfigError, load_config
from archcode.mcp import MCPManager
from archcode.memory import InstructionDocumentLoader
from archcode.permissions import PermissionChecker, PermissionMode, PathSandbox
from archcode.prompts import build_system_prompt
from archcode.tools import create_default_registry
from archcode.tools.tool_search import ToolSearchTool


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
    agent: Agent, prompt: str, mcp_manager: MCPManager | None
) -> None:
    conversation = ConversationManager()
    try:
        result = await agent.run_to_completion(prompt, conversation)
        for diagnostic in agent.last_instruction_diagnostics:
            location = str(diagnostic.source_path)
            if diagnostic.line is not None:
                location = f"{location}:{diagnostic.line}"
            print(
                f"[instructions] {diagnostic.severity}: {diagnostic.code} "
                f"({location}) — {diagnostic.message}",
                file=sys.stderr,
            )
        print(result, flush=True)
    finally:
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


def main() -> None:
    Path(".archcode").mkdir(parents=True, exist_ok=True)

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
    args = parser.parse_args()

    try:
        config_path = Path(args.config) if args.config else None
        config = load_config(config_path)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)

    work_dir = Path(args.work_dir).resolve() if args.work_dir else Path(os.getcwd())

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
                await _run_prompt(agent, args.p, mcp_manager)

            asyncio.run(_oneshot())
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

            app = ArchCodeApp(
                agent=agent,
                model_name=provider.model,
                driver_class=NoAltScreenDriver,
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
