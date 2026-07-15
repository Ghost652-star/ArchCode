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
from archcode.prompts import build_system_prompt
from archcode.tools import create_default_registry


async def _run_prompt(agent: Agent, prompt: str) -> None:
    conversation = ConversationManager()
    result = await agent.run_to_completion(prompt, conversation)
    print(result, flush=True)


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

    work_dir = Path(os.getcwd())
    system_prompt = build_system_prompt(
        work_dir=str(work_dir),
        extra=config.system_prompt,
    )

    # v0.2:创建默认工具注册中心,所有 6 个工具都启用
    tool_registry = create_default_registry(work_dir=work_dir)

    agent = Agent(
        client=client,
        system_prompt=system_prompt,
        tool_registry=tool_registry,
        max_output_tokens=provider.max_output_tokens,
    )

    try:
        if args.p is not None:
            asyncio.run(_run_prompt(agent, args.p))
        else:
            from archcode.app import ArchCodeApp
            from archcode.driver import NoAltScreenDriver

            app = ArchCodeApp(
                agent=agent,
                model_name=provider.model,
                driver_class=NoAltScreenDriver,
            )
            app.run()
    except LLMError as e:
        print(f"LLM error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

