"""Framework primitives for local Slash Commands.

The framework owns syntax parsing, name lookup and uniform handler invocation.
It deliberately does not know Textual, Agent internals or any command's business
rules; those belong to the app adapter and individual handlers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol


class CommandUI(Protocol):
    """Small UI surface available to every command handler."""

    def show_system(self, text: str) -> None:
        """Render a local system result without adding a conversation Message."""

    def show_error(self, text: str) -> None:
        """Render a local command failure without ending the input queue."""

    def status_text(self) -> str:
        """Return a local diagnostic summary."""

    async def run_agent_task(self, text: str) -> None:
        """Submit a normalized task through the current Agent path."""

    async def run_manual_compact(self, focus: str) -> None:
        """Run a user-requested context compaction."""

    async def clear_to_new_session(self) -> None:
        """Switch to a fresh session and reset its visible chat."""

    async def toggle_plan_mode(self, task: str) -> None:
        """Enter or leave Plan Mode, optionally submitting a planning task."""

    async def configure_permission(self, raw_args: str) -> None:
        """Handle the existing permission-mode controls."""

    async def resume_session(self, session_id: str) -> None:
        """Restore a stored session and its visible conversation."""


@dataclass(frozen=True)
class ParsedCommand:
    """The syntax-only result of parsing an input line."""

    is_command: bool
    name: str = ""
    raw_args: str = ""


@dataclass
class CommandContext:
    """Dependencies prepared by the dispatcher for one command invocation."""

    raw_args: str
    ui: CommandUI
    registry: "CommandRegistry"
    agent: Any = None
    conversation: Any = None
    session: Any = None
    session_manager: Any = None
    memory_manager: Any = None


CommandHandler = Callable[[CommandContext], Awaitable[None]]


@dataclass(frozen=True)
class CommandSpec:
    """Static, user-visible description and executable handler for one command."""

    name: str
    description: str
    usage: str
    handler: CommandHandler
    argument_hint: str = ""
    hidden: bool = False


def parse_command(text: str) -> ParsedCommand:
    """Recognize `/name` and retain everything after its first whitespace.

    No shell-like argument splitting happens here: command handlers own their
    subcommands and preserve natural-language task text.
    """

    trimmed = text.strip()
    if not trimmed.startswith("/"):
        return ParsedCommand(is_command=False)

    body = trimmed[1:]
    if not body:
        return ParsedCommand(is_command=True)

    parts = body.split(None, 1)
    name = parts[0]
    raw_args = parts[1] if len(parts) == 2 else ""
    return ParsedCommand(is_command=True, name=name.lower(), raw_args=raw_args)


class CommandRegistry:
    """In-memory mapping from formal command names to their specifications."""

    def __init__(self) -> None:
        self._by_name: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> None:
        name = spec.name.lower()
        if not name or any(char.isspace() for char in name) or name.startswith("/"):
            raise ValueError(f"invalid command name: {spec.name!r}")
        if name in self._by_name:
            raise ValueError(f"command already registered: {name}")
        if spec.name != name:
            spec = CommandSpec(
                name=name,
                description=spec.description,
                usage=spec.usage,
                handler=spec.handler,
                argument_hint=spec.argument_hint,
                hidden=spec.hidden,
            )
        self._by_name[name] = spec

    def find(self, name: str) -> CommandSpec | None:
        return self._by_name.get(name.lower())

    def visible_commands(self) -> list[CommandSpec]:
        return sorted(
            (spec for spec in self._by_name.values() if not spec.hidden),
            key=lambda spec: spec.name,
        )


class CommandDispatcher:
    """Routes Slash input to a registered handler exactly once."""

    def __init__(self, registry: CommandRegistry) -> None:
        self._registry = registry

    async def dispatch(
        self,
        text: str,
        *,
        ui: CommandUI,
        agent: Any = None,
        conversation: Any = None,
        session: Any = None,
        session_manager: Any = None,
        memory_manager: Any = None,
    ) -> bool:
        parsed = parse_command(text)
        if not parsed.is_command:
            return False
        if not parsed.name:
            ui.show_system(self._format_help())
            return True

        spec = self._registry.find(parsed.name)
        if spec is None:
            ui.show_system(f"未知命令: /{parsed.name}\n输入 /help 查看可用命令。")
            return True
        if not parsed.raw_args and spec.argument_hint:
            ui.show_system(spec.argument_hint)
            return True

        try:
            await spec.handler(
                CommandContext(
                    raw_args=parsed.raw_args,
                    ui=ui,
                    registry=self._registry,
                    agent=agent,
                    conversation=conversation,
                    session=session,
                    session_manager=session_manager,
                    memory_manager=memory_manager,
                )
            )
        except Exception as exc:
            ui.show_error(f"命令执行失败: {type(exc).__name__}: {exc}")
        return True

    def _format_help(self) -> str:
        rows = ["可用命令："]
        rows.extend(
            f"  /{spec.name:<12} {spec.description}"
            for spec in self._registry.visible_commands()
        )
        rows.append("输入 /help <命令> 查看详细用法。")
        return "\n".join(rows)
