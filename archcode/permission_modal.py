"""PermissionModal —— HITL 内联弹窗（照搬 MewCode InlinePermissionWidget）。

支持两种模式：
- 权限询问（options=None）：Yes / No
- AskUserQuestion（options=[...]）：每个 option 一个选项
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message as TMessage
from textual.widgets import Static

_log = logging.getLogger("archcode.perm")


_PERM_OPTIONS = [
    ("Yes", True),
    ("No", False),
]


class PermissionModal(Vertical, can_focus=True):
    """HITL 内联弹窗，可获焦的 Vertical 容器。"""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "deny", "Deny", priority=True),
    ]

    class Responded(TMessage):
        """用户做出选择后发送此消息。value 是 bool 或 selected label 字符串。"""

        def __init__(self, value) -> None:
            super().__init__()
            self.value = value

    def __init__(
        self,
        tool_name: str,
        description: str,
        question: str | None = None,
        options: list | None = None,
        on_select=None,
        **kwargs,
    ) -> None:
        super().__init__(id="perm-inline", classes="perm-modal", **kwargs)
        self._tool_name = tool_name
        self._description = description
        self._question = question
        self._options = options
        self._cursor = 0
        self._on_select = on_select

    def compose(self):
        yield Static(self._build_content(), id="perm-content")

    def on_mount(self) -> None:
        self._dump()
        self.focus()

    def _option_count(self) -> int:
        # 空 options 当作没提供,回退到默认 Yes/No,保证弹窗永远可交互
        if self._options:
            return len(self._options)
        return len(_PERM_OPTIONS)

    def _dump(self) -> None:
        """调试输出:logging 进 archcode.log + 独立写入 hitl-debug.log。"""
        log_path = Path(os.getcwd()) / ".archcode" / "hitl-debug.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n--- {time.strftime('%H:%M:%S')} ---\n")
                f.write(f"tool_name={self._tool_name!r}\n")
                f.write(f"description={self._description!r}\n")
                f.write(f"question={self._question!r}\n")
                f.write(f"options={self._options!r}\n")
                f.write(f"option_count={self._option_count()}\n")
                f.flush()
        except OSError as e:
            _log.warning("写 hitl-debug.log 失败: %s", e)
        _log.info(
            "PermissionModal mounted: tool=%r question=%r option_count=%d",
            self._tool_name,
            self._question,
            self._option_count(),
        )

    def _option_label(self, i: int) -> str:
        if self._options:
            opt = self._options[i]
            if isinstance(opt, dict):
                return opt.get("label", f"Option {i+1}")
            return str(opt)
        return _PERM_OPTIONS[i][0]

    def _option_value(self, i: int):
        if self._options:
            opt = self._options[i]
            if isinstance(opt, dict):
                return opt.get("label", "")
            return str(opt)
        return _PERM_OPTIONS[i][1]

    def _build_content(self) -> str:
        lines = []
        lines.append("")

        if self._question:
            lines.append(f"  [bold yellow]❓ {self._question}[/bold yellow]\n")
        else:
            lines.append(f"  [bold yellow]⚠ {self._tool_name}[/bold yellow]\n")

        if self._description and self._description != "需要用户确认":
            lines.append(f"    {self._description}\n")
        if self._question:
            lines.append("  [dim]请选择:[/dim]\n")
        else:
            lines.append("  [dim]This command requires approval[/dim]\n")
            lines.append("  Do you want to proceed?\n")

        if not self._options:
            # 模型没提供选项:提示用户,回退到默认 Yes/No
            lines.append("  [dim](模型未提供选项，按 Yes/No 回答)[/dim]\n")

        for i in range(self._option_count()):
            label = self._option_label(i)
            if i == self._cursor:
                lines.append(f" [bold cyan]❯[/bold cyan] {i + 1}. [bold]{label}[/bold]")
            else:
                lines.append(f"   {i + 1}. [dim]{label}[/dim]")
            if self._options and isinstance(self._options[i], dict):
                desc = self._options[i].get("description", "")
                if desc:
                    lines.append(f"        [dim]{desc}[/dim]")

        return "\n".join(lines)

    def _refresh(self) -> None:
        try:
            content = self.query_one("#perm-content", Static)
            content.update(self._build_content())
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._cursor < self._option_count() - 1:
            self._cursor += 1
            self._refresh()

    def action_select(self) -> None:
        if self._on_select is not None:
            self._on_select(self._option_value(self._cursor))
        self.remove()

    def action_deny(self) -> None:
        if self._on_select is not None:
            # 有真实选项 → 用户没选,回空字符串;否则(Yes/No 回退) → 拒绝
            value = "" if self._options else False
            self._on_select(value)
        self.remove()