from __future__ import annotations

from archcode.prompts.system import DEFAULT_SYSTEM_PROMPT


def build_system_prompt(work_dir: str | None = None, extra: str = "") -> str:
    parts = [DEFAULT_SYSTEM_PROMPT.strip()]

    if work_dir:
        parts.append(f"Working directory: {work_dir}")

    if extra:
        parts.append(extra.strip())

    return "\n\n".join(parts)
