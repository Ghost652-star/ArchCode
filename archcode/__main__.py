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
from archcode.permissions import PermissionChecker, PermissionMode, PathSandbox
from archcode.prompts import build_system_prompt
from archcode.tools import create_default_registry
from archcode.tools.tool_search import ToolSearchTool


async def _setup_registry_and_mcp(config, work_dir, protocol):
    """异步初始化:建默认 registry + 注册 ToolSearch + 连 MCP server。

    Returns: (registry, mcp_manager_or_None, mcp_errors)
    """
    tool_registry = create_default_registry(work_dir=work_dir)
    tool_registry.register(ToolSearchTool(tool_registry, protocol=protocol))

    mcp_manager: MCPManager | None = None
    mcp_errors: list[str] = []
    if config.mcp_servers:
        mcp_manager = MCPManager()
        mcp_manager.load_configs(config.mcp_servers)
        try:
            mcp_errors = await mcp_manager.register_all_tools(tool_registry)
        except Exception as e:
            print(f"[MCP init error] {e}", file=sys.stderr)

    return tool_registry, mcp_manager, mcp_errors


async def _run_prompt(agent: Agent, prompt: str, mcp_manager: MCPManager | None) -> None:
    conversation = ConversationManager()
    try:
        result = await agent.run_to_completion(prompt, conversation)
        print(result, flush=True)
    finally:
        if mcp_manager is not None:
            await mcp_manager.shutdown()


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
            "项目工作目录。工具读写的相对路径基准,plan 文件落盘位置(.archcode/plans/)。"
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

    provider = config.providers[0]
    try:
        client = create_client(provider)
    except AuthenticationError as e:
        print(f"Auth error: {e}", file=sys.stderr)
        sys.exit(1)

    # 工作目录:CLI 显式指定优先,否则用 cwd
    work_dir = Path(args.work_dir).resolve() if args.work_dir else Path(os.getcwd())

    system_prompt = build_system_prompt(
        work_dir=str(work_dir),
        extra=config.system_prompt,
    )

    # 异步初始化 tool registry + MCP manager
    tool_registry, mcp_manager, mcp_errors = asyncio.run(
        _setup_registry_and_mcp(config, work_dir, provider.protocol)
    )
    for err in mcp_errors:
        print(f"[MCP warning] {err}", file=sys.stderr)

    # 权限系统:路径沙箱 + 权限检查器
    sandbox = PathSandbox(project_root=str(work_dir))
    permission_checker = PermissionChecker(
        sandbox=sandbox,
        mode=PermissionMode.DEFAULT,
    )

    agent = Agent(
        client=client,
        system_prompt=system_prompt,
        tool_registry=tool_registry,
        permission_checker=permission_checker,
        max_output_tokens=provider.max_output_tokens,
        work_dir=work_dir,
        # 注意:不再有 CLI --plan 启动选项,plan 模式只能在 TUI 内通过 /plan 进入
    )

    try:
        if args.p is not None:
            asyncio.run(_run_prompt(agent, args.p, mcp_manager))
        else:
            from archcode.app import ArchCodeApp
            from archcode.driver import NoAltScreenDriver

            app = ArchCodeApp(
                agent=agent,
                model_name=provider.model,
                driver_class=NoAltScreenDriver,
            )
            # 保存 manager 引用,app 退出时清理
            app._mcp_manager = mcp_manager
            app.run()
            # run() 阻塞直到用户退出
            if mcp_manager is not None:
                asyncio.run(mcp_manager.shutdown())
    except LLMError as e:
        print(f"LLM error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()