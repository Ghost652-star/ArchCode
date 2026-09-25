"""Agent 定义文件解析:YAML frontmatter → AgentDef(sub-agent-design §4.1)。

校验(§4.1):缺 name / description → 报错;permissionMode / isolation 取值
越界 → 报错;maxTurns 非正整数 → 报错。`model` 只做类型检查——不绑定厂商
命名(§4.4.1 留白决策),留白 / "inherit" = 沿用父,其他值按别名在 selectLLM
解析(解析失败回落父 client)。非法文件由 loader 捕获跳过 + 诊断,不阻断启动。
frontmatter 解析骨架复用 skills 的实现(§4.8:结构相同、解析可复用)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from archcode.agents.models import AgentDef
from archcode.skills.loader import parse_frontmatter

VALID_PERMISSION_MODES = {"default", "acceptEdits", "dontAsk"}
VALID_ISOLATION_MODES = {"", "worktree"}


class AgentParseError(Exception):
    pass


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise AgentParseError(f"{field_name} 必须是字符串列表")
    return [v.strip() for v in value if v.strip()]


def parse_agent_file(path: Path) -> AgentDef:
    """解析一个 agent 定义文件;任何问题抛 AgentParseError(由 loader 跳过)。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentParseError(f"无法读取定义文件 {path}: {exc}") from exc
    return parse_agent_text(raw, source_path=path)


def parse_agent_text(raw: str, source_path: Path | None = None) -> AgentDef:
    """解析定义文本(内置层经 importlib.resources 读的是文本而非路径)。"""
    meta, body, err = parse_frontmatter(raw)
    if meta is None or err is not None:
        raise AgentParseError(err or "frontmatter 解析失败")

    name = str(meta.get("name", "")).strip()
    if not name:
        raise AgentParseError("缺少 name")
    description = str(meta.get("description", "")).strip()
    if not description:
        raise AgentParseError("缺少 description")

    max_turns = meta.get("maxTurns", 50)
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns <= 0:
        raise AgentParseError(f"maxTurns 必须是正整数: {max_turns!r}")

    permission_mode = str(meta.get("permissionMode", "")).strip() or "default"
    if permission_mode not in VALID_PERMISSION_MODES:
        raise AgentParseError(
            f"permissionMode 非法(只允许 default/acceptEdits/dontAsk): {permission_mode!r}"
        )

    isolation = str(meta.get("isolation", "")).strip()
    if isolation not in VALID_ISOLATION_MODES:
        raise AgentParseError(f"isolation 非法(只允许 worktree): {isolation!r}")

    background = meta.get("background", False)
    if not isinstance(background, bool):
        raise AgentParseError(f"background 必须是布尔值: {background!r}")

    model = str(meta.get("model", "") or "").strip() or "inherit"

    return AgentDef(
        agent_type=name,
        when_to_use=description,
        system_prompt=body,
        tools=_string_list(meta.get("tools"), "tools"),
        disallowed_tools=_string_list(meta.get("disallowedTools"), "disallowedTools"),
        model=model,
        max_turns=max_turns,
        permission_mode=permission_mode,
        background=background,
        isolation=isolation,
        file_path=source_path,
        source="builtin",
    )
