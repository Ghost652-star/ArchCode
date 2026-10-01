from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from archcode.agent import Agent
from archcode.conversation.manager import ConversationManager
from archcode.llm.client import AuthenticationError, LLMError
from archcode.config import ConfigError, load_config
from archcode.mcp import MCPManager
from archcode.memory import (
    SessionManager,
    format_instruction_diagnostics,
)
from archcode.paths import debug_log_path, project_data_dir
from archcode.runtime import (
    _wire_agents,
    _wire_hooks,
    _wire_skills,
    build_agent_sync as _build_agent_sync,
    build_tool_registry,
    connect_mcp as _connect_mcp,
)

_log = logging.getLogger("archcode")  # 运行时 __name__=="__main__",显式包级名


async def _build_runtime(config, work_dir, protocol):
    """异步初始化:建默认 registry + 注册 ToolSearch + 连 MCP server。

    Returns: (tool_registry, mcp_manager_or_None, mcp_errors, mcp_successes)
    """
    tool_registry = build_tool_registry(work_dir=work_dir, protocol=protocol)
    mcp_manager, mcp_errors, mcp_successes = await _connect_mcp(
        config.mcp_servers, tool_registry
    )
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
    except RuntimeError as e:
        _log.error("oneshot run failed: %s", e)
        raise
    finally:
        session.close()
        if mcp_manager is not None:
            await mcp_manager.shutdown()


def _setup_logging(work_dir: Path) -> None:
    """日志配置:项目级 debug.log(追加) + [sess/task] 关联列。

    - 默认 INFO,ARCHCODE_LOG_LEVEL 可覆盖(非法值回落 INFO);
    - CorrelationFilter 在 emit 时注入 session/task 关联列(语义见 logctx);
    - 存量 getLogger 处(compactor / hooks / mcp / webui)零改动继承新格式。
    幂等:root 已挂带 _archcode_log 标记的 handler 时直接返回。
    """
    from archcode.logctx import CorrelationFilter

    level = getattr(logging, os.environ.get("ARCHCODE_LOG_LEVEL", "INFO").upper(), None)
    if not isinstance(level, int):
        level = logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        if getattr(handler, "_archcode_log", False):
            return
    # 目录由 main() 先建;这里再兜底一次,函数自包含(直接调用时不依赖调用序)
    debug_log_path(work_dir).parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(debug_log_path(work_dir), mode="a", encoding="utf-8")
    handler._archcode_log = True
    handler.addFilter(CorrelationFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(sess)s/%(task)s] %(name)s %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    _log.info("logging ready: file=%s level=%s", debug_log_path(work_dir),
               logging.getLevelName(level))


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

    _log.info(
        "archcode start: mode=%s work_dir=%s provider=%s model=%s",
        "oneshot" if args.p is not None else ("web" if args.web else "tui"),
        work_dir,
        config.providers[0].protocol,
        config.providers[0].model,
    )

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
            # Web 路径:多会话并行——每对话的运行时由注册表按需构建
            # (archcode/runtime.py 工厂),MCP 连接按工作区共享(惰性连接)。
            from archcode.webui.server import run_web

            run_web(work_dir=work_dir, config=config, port=args.port)
        else:
            # TUI 路径:build 同步做(create_default_registry 不需要 await),
            # MCP 连接放到 background task,在 TUI 的 event loop 里跑。
            # 这样 stdio_client 的 task group 跟 TUI 是同一个 event loop。
            from archcode.app import ArchCodeApp
            from archcode.driver import NoAltScreenDriver

            provider = config.providers[0]
            tool_registry = build_tool_registry(work_dir, provider.protocol)

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
