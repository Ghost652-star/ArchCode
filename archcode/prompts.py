from __future__ import annotations

from pathlib import Path


DEFAULT_SYSTEM_PROMPT = """\
You are ArchCode, a helpful AI coding assistant running in the terminal.
Answer clearly and concisely. When writing code, use markdown code blocks.
"""


def build_system_prompt(work_dir: str | None = None, extra: str = "") -> str:
    parts = [DEFAULT_SYSTEM_PROMPT.strip()]

    if work_dir:
        parts.append(f"Working directory: {work_dir}")

    if extra:
        parts.append(extra.strip())

    return "\n\n".join(parts)
