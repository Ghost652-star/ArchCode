"""PermissionModal —— 聊天流内嵌选择器（照搬 MewCode InlinePermissionWidget / InlineAskUserWidget）。

统一处理两种场景：
- 权限询问（options 为空）：Yes / No
- AskUserQuestion（options 非空）：渲染选项列表，方向键 + 数字热键 + 多选

结果通过 post_message(Responded) 冒泡给 App，App 在
on_permission_modal_responded 里回填 future、移除弹窗、重新启用输入框。
"""

from __future__ import annotations

from rich.markup import escape
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message as TMessage
from textual.widgets import Static


_PERM_OPTIONS = [
    ("Yes", True),
    ("No", False),
]

# 数字热键支持的最大选项数（设计文档：1-9 直选）
_MAX_NUM_KEYS = 9


class PermissionModal(Vertical, can_focus=True):
    """聊天流内嵌的选项选择器。

    特性（对照设计文档 §4）：
    - ↑/↓ 移动光标，数字热键 1-9 直选，Enter 确认，Esc 取消
    - multi_select=True 时 space 切换多选
    - 结果 post_message(Responded(value)) 冒泡
    """

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("space", "toggle", "Toggle", priority=True),
        *[
            Binding(str(i + 1), f"select_index({i})", f"Pick {i + 1}", show=False)
            for i in range(_MAX_NUM_KEYS)
        ],
    ]

    class Responded(TMessage):
        """选择结果冒泡消息。value 是 bool（权限）或 label 字符串（AskUser）。"""

        def __init__(self, value) -> None:
            super().__init__()
            self.value = value

    def __init__(
        self,
        tool_name: str,
        description: str,
        question: str | None = None,
        options: list | None = None,
        multi_select: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(id="perm-inline", classes="perm-modal", **kwargs)
        self._tool_name = tool_name
        self._description = description
        self._question = question
        self._options = options
        self._multi_select = multi_select
        self._cursor = 0  # ★ 核心状态：光标
        self._selected: set[int] = set()  # 多选时记录选中的索引

    def compose(self) -> TMessage | Static:
        yield Static(self._build_content(), id="perm-content")

    def on_mount(self) -> None:
        self.focus()

    # ── 选项数据 ────────────────────────────────────────────────

    def _is_askuser(self) -> bool:
        return self._question is not None

    def _option_count(self) -> int:
        # 空 options 当作没提供，回退到默认 Yes/No，保证永远可交互
        if self._options:
            return len(self._options)
        return len(_PERM_OPTIONS)

    def _option_label(self, i: int) -> str:
        if self._options:
            opt = self._options[i]
            if isinstance(opt, dict):
                return opt.get("label", f"Option {i + 1}")
            return str(opt)
        return _PERM_OPTIONS[i][0]

    def _option_value(self, i: int):
        if self._options:
            opt = self._options[i]
            if isinstance(opt, dict):
                return opt.get("value", opt.get("label", ""))
            return str(opt)
        return _PERM_OPTIONS[i][1]

    # ── 视图：纯函数由 _cursor/_selected 推导 ───────────────────

    def _build_content(self) -> str:
        lines = []
        lines.append("")

        if self._question:
            # 近似 MewCode 的 color(99)（ANSI 256 #6633ff），Textual 用 hex 才能解析
            lines.append(f" [bold #9d7bff]{escape(self._question)}[/]\n")
        else:
            lines.append(f" [bold yellow]⚠ {escape(self._tool_name)}[/bold yellow]\n")

        if self._description and self._description != "需要用户确认":
            lines.append(f"   {escape(self._description)}\n")

        for i in range(self._option_count()):
            label = escape(self._option_label(i))
            prefix = " ❯ " if i == self._cursor else "   "
            bold = "[bold]" if i == self._cursor else ""
            end = "[/]" if i == self._cursor else ""
            if self._multi_select and self._options:
                check = "● " if i in self._selected else "○ "
            else:
                check = ""
            lines.append(f"{prefix}{check}{bold}{label}{end}")

            if self._options and isinstance(self._options[i], dict):
                desc = self._options[i].get("description", "")
                if desc:
                    lines.append(f"        [dim]{escape(desc)}[/dim]")

        if self._multi_select and self._options:
            lines.append("\n   [dim]↑↓ 选择 · space 多选 · Enter 确认 · Esc 取消[/dim]")
        else:
            lines.append("\n   [dim]↑↓ 选择 · Enter 确认 · Esc 取消[/dim]")

        return "\n".join(lines)

    def _refresh(self) -> None:
        # 任何状态变更后整段重绘（设计文档 §5.3：Static.update 是最快路径）
        try:
            self.query_one("#perm-content", Static).update(self._build_content())
        except Exception:
            pass

    # ── Controller：action_* ────────────────────────────────────

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._cursor < self._option_count() - 1:
            self._cursor += 1
            self._refresh()

    def action_toggle(self) -> None:
        if not (self._multi_select and self._options):
            return
        if self._cursor < self._option_count():
            if self._cursor in self._selected:
                self._selected.discard(self._cursor)
            else:
                self._selected.add(self._cursor)
            self._refresh()

    def _emit(self, value) -> None:
        self.post_message(self.Responded(value))

    def _select(self, idx: int) -> None:
        if idx < 0 or idx >= self._option_count():
            return
        if self._multi_select and self._options:
            # 多选：提交当前已选项（若一个都没勾，用光标所在项兜底）
            chosen = sorted(self._selected) if self._selected else [idx]
            value = ", ".join(self._option_label(i) for i in chosen)
        else:
            value = self._option_value(idx)
        self._emit(value)

    def action_select(self) -> None:
        self._select(self._cursor)

    def action_select_index(self, idx: int) -> None:
        self._select(idx)

    def action_cancel(self) -> None:
        # 权限:False（拒绝）; AskUser:""（没选）
        value = "" if self._is_askuser() else False
        self._emit(value)