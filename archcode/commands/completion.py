"""Pure Slash Command completion lookup.

This module deliberately contains no Textual widgets or keyboard handling.  It
turns the current input prefix plus a CommandRegistry into display-ready command
data; the App decides where and how that data is rendered.
"""

from __future__ import annotations

from dataclasses import dataclass

from archcode.commands.core import CommandRegistry


@dataclass(frozen=True)
class CommandCompletion:
    """One visible formal command candidate for an input prefix."""

    name: str
    description: str


def complete_commands(
    registry: CommandRegistry, input_text: str
) -> list[CommandCompletion]:
    """Return formal visible commands matching a root Slash Command prefix.

    Subcommand completion is intentionally out of scope: once whitespace
    appears after the slash command, its handler owns the argument grammar.
    """

    if not input_text.startswith("/"):
        return []
    body = input_text[1:]
    if any(char.isspace() for char in body):
        return []
    prefix = body.lower()
    return [
        CommandCompletion(name=spec.name, description=spec.description)
        for spec in registry.visible_commands()
        if spec.name.startswith(prefix)
    ]
