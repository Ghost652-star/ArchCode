from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message as TMessage
from textual.reactive import reactive
from textual.widgets import Footer, Header, Markdown, Static, TextArea
from rich.text import Text as RichText

from archcode.agent import (
    Agent,
    CompactFinished,
    CompactProgress,
    CompactStarted,
    ErrorEvent,
    InstructionDiagnosticsEvent,
    LoopComplete,
    PermissionRequest,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)
from archcode.conversation.manager import ConversationManager
from archcode.memory import SessionManager, format_instruction_diagnostics
from archcode.permissions import PermissionMode
from archcode.permission_modal import PermissionModal


# 思考状态显示（参考 MewCode）
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
THINKING_VERBS = [
    "Accomplishing", "Architecting", "Baking", "Beboppin'", "Befuddling",
    "Bloviating", "Boogieing", "Boondoggling", "Bootstrapping", "Brewing",
    "Calculating", "Canoodling", "Caramelizing", "Cascading", "Cerebrating",
    "Choreographing", "Churning", "Coalescing", "Cogitating", "Combobulating",
    "Composing", "Computing", "Concocting", "Considering", "Contemplating",
    "Cooking", "Crafting", "Creating", "Crunching", "Crystallizing",
    "Cultivating", "Deciphering", "Deliberating", "Dilly-dallying",
    "Discombobulating", "Doodling", "Elucidating", "Enchanting", "Envisioning",
    "Fermenting", "Finagling", "Flambéing", "Flibbertigibbeting", "Flummoxing",
    "Forging", "Frolicking", "Fusillading", "Gallivanting", "Garnishing",
    "Generating", "Germinating", "Glittering", "Grokking", "Gusting",
    "Hackneying", "Hedonizing", "Hexing", "Hibernating", "Hobnobbing",
    "Hocus-pocusing", "Holographing", "Hypnotizing", "Imagining", "Improvising",
    "Incanting", "Incubating", "Inferring", "Infusing", "Inventing",
    "Invoking", "Jubblating", "Juggling", "Jury-rigging", "Kaleidoscoping",
    "Kerfuffling", "Kerning", "Kindling", "Kneading", "Knitting",
    "Leveling", "Lobbying", "Lollygagging", "Magnetizing", "Malarkeying",
    "Mandating", "Marinating", "Meandering", "Mesmerizing", "Milling",
    "Mischiefing", "Moseying", "Mulling", "Mummifying", "Mustering",
    "Nebulating", "Necromancing", "Nesting", "Noodling", "Nurturing",
    "Nuzzling", "Orbiting", "Origami-ing", "Oscillating", "Ostentatious",
    "Palindroming", "Pandering", "Pantomiming", "Parading", "Perambulating",
    "Percolating", "Perfecting", "Perorating", "Persisting", "Philosophizing",
    "Photosynthesizing", "Piddling", "Piloting", "Plagiarizing", "Pondering",
    "Pontificating", "Pouncing", "Precipitating", "Prestidigitating", "Privileging",
    "Processing", "Proofing", "Propagating", "Proselytizing", "Puttering",
    "Puzzling", "Quacking", "Quaffing", "Quarrelling", "Quibbling",
    "Quixoting", "Quizzing", "Razzle-dazzling", "Recalibrating", "Recombobulating",
    "Refactoring", "Reifying", "Reminiscing", "Reticulating", "Reveling",
    "Riffing", "Ruminating", "Sautéing", "Saxophoning", "Scandalmongering",
    "Scheming", "Scrounging", "Scrying", "Sculpting", "Sequencing",
    "Shenaniganing", "Skullduggerying", "Slapsticking", "Sleeping-in", "Snickering",
    "Sorting", "Spelunking", "Spinning", "Squelching", "Strategizing",
    "Sussing", "Symbolizing", "Synthesizing", "Tantalizing", "Tapestrying",
    "Tempering", "Thaumaturging", "Thwarting", "Tinkering", "Titivating",
    "Transmuting", "Triumphing", "Troubleshooting", "Twisting", "Typing",
    "Unfurling", "Unicycling", "Unraveling", "Upcycling", "Vacuuming",
    "Veering", "Verklempting", "Vibing", "Vibing", "Waddling",
    "Waffling", "Wandering", "Whatchamacalliting", "Whirlpooling", "Wibbling",
    "Wiggling", "Winking", "Witch-hunting", "Wizardizing", "Wooing",
    "Wrangling", "Xylophoning", "Yammering", "Yawning", "Yodeling",
    "Zestfully", "Zigzagging", "Zooming",
]


# 简单过去式转换（实际 MewCode 用 _to_past_tense，更复杂）
def _to_past_tense(verb: str) -> str:
    """简易过去式：去 e + ed 或直接 + ed"""
    if verb.endswith("e"):
        return verb + "d"
    if verb.endswith("ing"):
        return verb  # already ing-like
    return verb + "ed"


class ChatInput(TextArea):
    BINDINGS = [
        Binding("enter", "submit", "Send", priority=True),
        Binding("shift+enter", "newline", "New line", priority=True),
        Binding("ctrl+j", "newline", "New line", priority=True),
    ]

    class Submitted(TMessage):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cursor_blink = False

    def action_submit(self) -> None:
        text = self.text.strip()
        if text:
            self.post_message(self.Submitted(text))
            self.clear()

    def action_newline(self) -> None:
        self.insert("\n")


class ArchCodeApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "ArchCode"

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+l", "clear_chat", "Clear", priority=True),
        Binding("escape", "abort_run", "Cancel", show=True, priority=True),
    ]

    # 状态栏 reactive —— 任何字段变都触发 watch,自动重渲染
    current_tokens: reactive[int] = reactive(0)
    context_window: reactive[int] = reactive(200_000)
    permission_mode_label: reactive[str] = reactive("default")

    def __init__(
        self,
        agent: Agent,
        model_name: str,
        *,
        driver_class: type | None = None,
    ) -> None:
        super().__init__(driver_class=driver_class)
        self._agent = agent
        self._model_name = model_name
        self._conversation = ConversationManager()
        self._session_manager = SessionManager(getattr(agent, "_work_dir", None) or Path.cwd())
        self._session = self._session_manager.create()
        self._session.bind(self._conversation)
        self._streaming = False
        self._agent_task: asyncio.Task[None] | None = None
        self._response_widget: Markdown | None = None
        self._response_buffer: list[str] = []
        self._pending_permission_future: asyncio.Future | None = None
        # MCP:由 __main__.py 传入配置,on_mount 里 background task 启动
        self._mcp_server_configs: list = []
        self._mcp_manager: MCPManager | None = None
        self._mcp_init_task: asyncio.Task | None = None
        # 上下文压缩状态(状态栏显示用,reactive 自动 watch 重渲染)
        client = getattr(agent, "_client", None)
        self.context_window = getattr(client, "context_window", 200_000)
        # 中断信号:用户按 Esc → set(),agent loop 每轮开头检查后退出
        # 同时挂到 agent._abort_event(实例属性),避免改 run() 签名
        self._abort_event: asyncio.Event = asyncio.Event()
        self._agent._abort_event = self._abort_event
        # Spinner 动起来:每 0.08s 切一帧
        self._spinner_idx: int = 0
        self._spinner_timer = None
        # 压缩进度 widget:存活的「正在压缩…」string + 收到字符数
        # None 表示当前没在压缩
        self._compact_widget: Static | None = None
        self._compact_chars: int = 0
        self._compact_mode: str = "auto"  # 记 Started 时的 mode,Progress 复用
        # AI 流式响应的加载点 ●(紫色, 单独 widget, 跟 Markdown 在同一 Horizontal)
        self._loading_dot: Static | None = None
        self._response_row: Horizontal | None = None
        # 初始读一次权限模式,后续 /permissions 切换时直接改 reactive
        try:
            checker = getattr(agent, "_permission_checker", None)
            if checker is not None:
                self.permission_mode_label = checker.mode.value
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        yield Static(self._make_banner(), id="title-bar")
        yield VerticalScroll(id="chat")
        yield ChatInput(
            id="input",
            placeholder="Send a message...",
        )
        with Horizontal(id="status-bar"):
            yield Static("default", id="mode-label")
            yield Static("", id="ctx-label")
            yield Static(self._model_name, id="model-label")

    def _make_banner(self) -> RichText:
        """顶栏 ASCII logo + 标题(模仿 MewCode 的 3 行布局)。

        左列紫色 ASCII 字符,右列灰字信息(version / model / work_dir)。
        RichText 保证 markdown 标签不被 Static 解析。
        """
        t = RichText()
        t.append(" /\\_/\\    ", style="bold color(99)")
        t.append("ArchCode v0.1.0\n", style="color(242)")
        t.append("( •.• )   ", style="bold color(99)")
        t.append(f"{self._model_name}\n", style="color(242)")
        t.append(" > ^ <    ", style="bold color(99)")
        try:
            wd = getattr(self._agent, "_work_dir", None)
            work_label = str(Path(wd)) if wd else "(cwd)"
        except Exception:
            work_label = "(cwd)"
        t.append(work_label, style="color(242)")
        return t

    async def on_mount(self) -> None:
        """App 挂载后:在 TUI 的 event loop 里 background task 启动 MCP。

        关键:不能用 __main__.py 里 asyncio.run 起 MCP——那样 stdio_client
        的 task group 跨 event loop,会死锁。这里用 create_task 让 MCP
        跟 TUI 同一个 loop。
        """
        if self._mcp_server_configs:
            self._mcp_init_task = asyncio.create_task(self._init_mcp())

    async def _init_mcp(self) -> None:
        """连接 MCP server + 注册工具到 registry。每个 server 的结果都报告。

        整段 try/except 兜底:任何异常都展示在聊天里,不让后台 task 静默死掉
        ——之前 _show_system_message 不存在导致整个 init 静默失败,工具永远不注册。
        """
        from archcode.mcp import MCPManager

        try:
            configs = self._mcp_server_configs
            if not configs:
                return

            names = [c.name for c in configs]
            self._show_system(
                f"[MCP] Connecting to {len(configs)} server(s): {', '.join(names)}"
            )

            manager = MCPManager()
            manager.load_configs(configs)
            errors, successes = await manager.register_all_tools(
                self._agent._tool_registry
            )
            self._mcp_manager = manager

            for name, count in successes:
                self._show_system(
                    f"[MCP] ✓ {name}: {count} tool(s) registered"
                )
            for err in errors:
                self._show_system(f"[MCP] ✗ {err}")

            self._show_system(
                f"[MCP] Done. {len(successes)}/{len(configs)} server(s) ready."
            )
        except Exception as e:
            # 兜底:任何 init 异常都打到聊天,避免静默失败导致工具永不注册
            self._show_system(f"[MCP] ✗ init crashed: {type(e).__name__}: {e}")

    async def on_unmount(self) -> None:
        """App 退出:关 MCP manager。"""
        self._session.close()
        if self._mcp_manager is not None:
            await self._mcp_manager.shutdown()
            self._mcp_manager = None

    def _status_bar_text(self) -> str:
        """保留原接口,内部用三栏更新。"""
        return (
            f"{self.permission_mode_label}  ·  "
            f"{self._ctx_label()}  ·  {self._model_name}"
        )

    def _ctx_label(self) -> str:
        """渲染上下文占用百分比。"""
        if self.context_window <= 0:
            return ""
        pct = (self.current_tokens / self.context_window) * 100
        cur = self._humanize_tokens(self.current_tokens)
        limit = self._humanize_tokens(self.context_window)
        return f"ctx: {cur} / {limit}  ({pct:.1f}%)"

    @staticmethod
    def _humanize_tokens(n: int) -> str:
        if n < 1000:
            return f"{n}"
        if n < 10_000:
            return f"{n / 1000:.1f}K"
        if n < 1_000_000:
            return f"{n // 1000}K"
        return f"{n / 1_000_000:.1f}M"

    def _update_ctx_tokens(self, input_tokens: int, cache_read: int = 0, cache_creation: int = 0) -> None:
        """更新状态栏的 current_tokens。UsageEvent 触发。

        用 set_reactive 强制刷新 —— 默认 reactive 相等值不触发 watch,
        但中转站/某些 provider 不返回 usage,input_tokens 一直 0,值不变就不会刷新。
        """
        # 与 ConversationManager.baseline_tokens 一致:input + cache_read + cache_creation
        new_total = max(
            input_tokens + cache_read + cache_creation, self.current_tokens
        )
        # set_reactive 强制触发 watch,即使值未变也能重渲染
        self.set_reactive(ArchCodeApp.current_tokens, new_total)

    def _get_work_dir_label(self) -> str:
        """从 agent 读当前工作目录,显示在状态栏。"""
        try:
            wd = getattr(self._agent, "_work_dir", None)
            if wd:
                return f"📁 {Path(wd)}"
        except Exception:
            pass
        return "📁 (cwd)"

    def watch_current_tokens(self, _new: int) -> None:
        self._refresh_status_bar()

    def watch_context_window(self, _new: int) -> None:
        self._refresh_status_bar()

    def watch_permission_mode_label(self, _new: str) -> None:
        self._refresh_status_bar()

    def _refresh_status_bar(self) -> None:
        """任一 reactive 字段变化都触发,更新状态栏三栏各自文本。"""
        try:
            self.query_one("#mode-label", Static).update(
                self.permission_mode_label
            )
            self.query_one("#ctx-label", Static).update(self._ctx_label())
            # model-label 不变,无需 update
        except Exception:
            # widget 还没 mount 或已 detach,忽略 —— on_mount 后 watch 会再触发
            pass

    def _update_status_bar(self) -> None:
        """兼容老调用点。"""
        self._refresh_status_bar()

    def _chat(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    def _input(self) -> ChatInput:
        return self.query_one("#input", ChatInput)

    def _set_input_enabled(self, enabled: bool) -> None:
        self._input().disabled = not enabled

    def on_permission_modal_responded(
        self, event: PermissionModal.Responded
    ) -> None:
        """权限/提问弹窗用户做出选择 → 回填 future、移除弹窗、恢复输入框。

        照搬 MewCode on_inline_permission_widget_responded：
        - future.set_result 让 agent 的 _execute_tool 继续
        - 移除弹窗，避免再次接收按键
        - 重新启用输入框并聚焦
        """
        req = self._pending_permission_future
        if req is not None and not req.done():
            req.set_result(event.value)
            self._pending_permission_future = None
        try:
            modal_widget = self.query_one("#perm-inline", PermissionModal)
            modal_widget.remove()
        except Exception:
            pass
        # 弹窗结束后恢复输入
        self._set_input_enabled(True)
        self._input().focus()

    def _append_message(self, widget: Static | Markdown, *, scroll: bool = True) -> None:
        chat = self._chat()
        chat.mount(widget)
        if scroll:
            widget.scroll_visible()

    def _show_system(self, text: str) -> None:
        self._append_message(Static(text, classes="system-msg"))

    def _show_error(self, text: str) -> None:
        self._append_message(Static(text, classes="error-msg"))

    def _show_compact_progress(self, mode: str, chars: int) -> None:
        """挂载 / 更新压缩进度 widget(MewCode 风格的 loading row)。

        首次调用时新挂一个 Static,后续只 update 文本,不重新挂。
        文本格式:[compact] 正在压缩… 收到 1,234 chars
        颜色用 system-msg(灰色斜体),跟普通日志区分开。
        """
        if self._compact_widget is None:
            self._compact_widget = Static("", classes="system-msg", markup=False)
            self._append_message(self._compact_widget, scroll=False)
        mode_label = {"auto": "自动", "manual": "手动", "force": "强制"}.get(
            mode, mode
        )
        self._compact_widget.update(
            f"[compact] {mode_label}压缩中… 收到 {chars:,} chars"
        )

    def _hide_compact_progress(self) -> None:
        """卸载压缩进度 widget(完成后调用)。"""
        if self._compact_widget is not None:
            try:
                self._compact_widget.remove()
            except Exception:
                pass
            self._compact_widget = None
            self._compact_chars = 0

    @staticmethod
    def _summarize_arg(key: str, value: object) -> str:
        """把工具参数折叠成一行摘要,避免 WriteFile(content=...) 把整段文件刷屏。"""
        # 1. 短字段(< 60 字符)直接 repr
        s = repr(value)
        if len(s) <= 60:
            return f"{key}={s}"
        # 2. 字符串 / bytes:显示字符数 + 前 30 字符
        if isinstance(value, (str, bytes)):
            n = len(value)
            preview = s[:30].rstrip("'\"")
            return f"{key}=<{n:,} chars> \"{preview}…\""
        # 3. dict / list:显示条目数
        if isinstance(value, dict):
            return f"{key}=<dict {len(value)} keys>"
        if isinstance(value, (list, tuple)):
            return f"{key}=<list {len(value)} items>"
        # 4. 其它:截断 repr
        return f"{key}={s[:60]}…"

    @staticmethod
    def _tool_title(tool_name: str, arguments: dict) -> str:
        """按工具名生成简短标题(只显示 basename + 行数,不展示正文)。

        仿照 MewCode app.py:310-330。Args dict 永远不进 UI 标题,
        避免 WriteFile(content=...) 整段刷屏。
        """
        import os
        path = arguments.get("file_path") or arguments.get("path") or ""
        short_path = os.path.basename(path) if path else ""
        if tool_name in ("ReadFile", "Read"):
            return f"Read {short_path}" if short_path else "Read"
        if tool_name in ("WriteFile", "Write"):
            content = arguments.get("content", "")
            lines = content.count("\n") + 1 if content else 0
            return f"Write {short_path} ({lines} lines)" if short_path else f"Write ({lines} lines)"
        if tool_name in ("EditFile", "Edit"):
            return f"Edit {short_path}" if short_path else "Edit"
        if tool_name == "Bash":
            cmd = arguments.get("command", "") or ""
            short = cmd[:50] + "…" if len(cmd) > 50 else cmd
            return f"Bash: {short}" if short else "Bash"
        if tool_name == "Glob":
            return f"Glob: {arguments.get('pattern', '')}"
        if tool_name == "Grep":
            return f"Grep: {arguments.get('pattern', '')}"
        return tool_name

    def _show_tool_use(self, event: ToolUseEvent) -> None:
        """显示工具调用:用 _tool_title 折叠成短标题(避免大段 args 刷屏)。

        格式:● Bash: dir …   ← 工具名 + 简短摘要 + loading 状态
        颜色:tool-block-loading (灰色 muted),完成后切到 tool-block (正常色)
        """
        title = self._tool_title(event.tool_name, event.arguments)
        self._append_message(
            Static(f"  ● {title} …", classes="tool-block tool-block-loading")
        )

    def _tick_spinner(self) -> None:
        """定时器回调:每 0.08s 切一帧 spinner,更新 thinking widget。"""
        if self._thinking_widget is None:
            return
        self._spinner_idx = (self._spinner_idx + 1) % len(SPINNER_FRAMES)
        frame = SPINNER_FRAMES[self._spinner_idx]
        elapsed = time.monotonic() - self._thinking_start
        self._thinking_widget.update(
            f"  {frame} {self._thinking_verb}…  ({elapsed:.0f}s)"
        )

    def _stop_spinner(self) -> None:
        """停掉 spinner 定时器(进入下一段/退出时调用)。"""
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def _show_tool_result(self, event: ToolResultEvent) -> None:
        """显示工具结果:仅名称 + 耗时,不展示实际 output(已喂给 LLM)。

        格式:✓ Bash (0ms)   ← 简洁一行,跟 `● Bash: dir …` 配对
        error 用 ✗ + tool-block-error 样式
        """
        elapsed_ms = event.elapsed * 1000
        if event.is_error:
            self._append_message(
                Static(
                    f"  ✗ {event.tool_name} ({elapsed_ms:.0f}ms)",
                    classes="tool-block tool-block-error",
                )
            )
        else:
            self._append_message(
                Static(
                    f"  ✓ {event.tool_name} ({elapsed_ms:.0f}ms)",
                    classes="tool-block",
                )
            )

    def _show_thinking(self, text: str) -> None:
        """显示模型思考文本:斜体灰色,与正文视觉区分。"""
        self._append_message(Static(text, classes="thinking-msg", markup=False))

    def action_clear_chat(self) -> None:
        self._conversation.clear()
        self._chat().remove_children()
        self._show_system("对话已清空。")

    def action_abort_run(self) -> None:
        """Esc:中断当前正在跑的 agent 循环。

        设置 asyncio.Event,agent loop 在下一轮开头或下一个 stream 事件点检查后退出。
        不直接 cancel task,避免 _client SDK 半路抛 CancelledError 把
        conversation / stream collector 状态搞乱。
        """
        if self._agent_task is not None and not self._agent_task.done():
            self._abort_event.set()
            self._show_system("[abort] 已请求取消,等待当前轮次结束…")
        else:
            self._show_system("[abort] 当前没有正在执行的任务。")

    def _set_plan_mode(self, on: bool) -> None:
        """切换 plan mode。开启时把 Agent 的 system prompt 注入 reminder,关闭时恢复。"""
        self._agent.set_plan_mode(on)
        if on:
            plan_path = getattr(self._agent, "_plan_path", None)
            label = str(plan_path) if plan_path else "(unknown)"
            self._show_system(f"Plan mode ON. 只读工具可用,写操作请用 /exit-plan 退出。\n   Plan file: {label}")
        else:
            self._show_system("Plan mode OFF. 已恢复正常操作。")
        self._update_status_bar()

    def _set_permission_mode(self, mode: PermissionMode) -> None:
        """切换权限模式（default / accept / bypass）。"""
        checker = getattr(self._agent, "_permission_checker", None)
        if checker is None:
            self._show_error("权限系统未初始化，无法切换模式。")
            return
        # plan mode 只能通过 /plan /exit-plan 进入/退出，不能通过 /mode
        if mode == PermissionMode.PLAN:
            self._show_system("Plan mode 请使用 /plan 或 /exit-plan 切换。")
            return
        checker.mode = mode
        # 直接改 reactive —— watch_permission_mode_label 会重渲染
        self.permission_mode_label = mode.value
        self._show_system(f"权限模式已切换为: {mode.value}")

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        text = event.text.strip()
        if not text:
            return

        if text.lower() in ("/quit", "/exit"):
            self.exit()
            return
        if text.lower() == "/clear":
            self.action_clear_chat()
            return
        if text.lower() == "/plan":
            self._set_plan_mode(True)
            return
        if text.lower() == "/exit-plan":
            self._set_plan_mode(False)
            return
        if text.lower() == "/compact":
            await self._handle_compact()
            return
        if text.lower().startswith("/mode"):
            parts = text.split(maxsplit=1)
            mode_str = parts[1].strip().lower() if len(parts) > 1 else ""
            mode_map = {
                "default": PermissionMode.DEFAULT,
                "accept": PermissionMode.ACCEPT,
                "bypass": PermissionMode.BYPASS,
                "plan": PermissionMode.PLAN,
            }
            if mode_str in mode_map:
                self._set_permission_mode(mode_map[mode_str])
            else:
                self._show_system(
                    "用法: /mode <default|accept|bypass|plan>\n"
                    f"  当前模式: {self.permission_mode_label}"
                )
            return

        if self._streaming:
            return

        # 照搬 MewCode（app.py:941）：agent 循环放独立 task，事件处理器立即返回。
        # 若在 handler 里 await 整个 agent 运行，HITL 等待期间 handler 一直挂起，
        # Textual 消息泵会阻塞，弹窗按键失去响应（设计文档 §8.7）。
        self._abort_event.clear()  # 新一轮:重置中断信号
        self._agent_task = asyncio.create_task(
            self._handle_user_message(text)
        )

    async def _handle_compact(self) -> None:
        """手动 /compact: 走 auto_compact(manual=True),不受阈值限制。"""
        compression = getattr(self._agent, "_compression", None)
        if compression is None or not compression.enabled:
            self._show_system("压缩未启用,无法执行 /compact。")
            return
        session_dir = getattr(self._agent, "_session_dir", None)
        if session_dir is None:
            self._show_system("session_dir 未初始化,无法执行 /compact。")
            return
        from archcode.context.compactor import auto_compact, CompactEvent

        # 手动路径不走 agent.run(),没有 Compact* 事件流;
        # 直接调进度 widget 维护,跟 auto 路径共用同一 widget。
        self._compact_chars = 0
        self._compact_mode = "manual"
        self._show_compact_progress("manual", 0)

        def _on_progress(delta: str) -> None:
            self._compact_chars += len(delta)
            self._show_compact_progress("manual", self._compact_chars)

        try:
            event = await auto_compact(
                conversation=self._conversation,
                client=self._agent._client,
                context_window=self.context_window,
                session_dir=session_dir,
                recovery=self._agent._recovery_state,
                tool_schemas=self._agent._tool_schemas(),
                breaker=self._agent._auto_compact_breaker,
                manual=True,
                recovery_file_limit=compression.recovery_file_limit,
                recovery_tokens_per_file=compression.recovery_tokens_per_file,
                recovery_skills_budget=compression.recovery_skills_budget,
                recovery_tokens_per_skill=compression.recovery_tokens_per_skill,
                on_text_delta=_on_progress,
            )
        except Exception as e:
            self._hide_compact_progress()
            self._show_system(f"[compact] 异常: {type(e).__name__}: {e}")
            return

        self._hide_compact_progress()
        if isinstance(event, CompactEvent):
            # 压缩成功:重置当前 token 显示(让下一轮 UsageEvent 重新锚定)
            self.current_tokens = 0
            self._update_status_bar()
            snippet = event.summary[:200].replace("\n", " ")
            self._show_system(
                f"[compact] ✓ 压缩完成,丢弃 {event.dropped_messages} 条消息。\n"
                f"   摘要预览: {snippet}{'...' if len(event.summary) > 200 else ''}"
            )
        elif isinstance(event, str):
            self._show_system(f"[compact] ✗ {event}")
        else:
            self._show_system("[compact] 无可压缩内容(history 为空或全在保留窗口内)。")

    async def _handle_user_message(self, text: str) -> None:
        # 用户消息:❯ 浅蓝前缀 + 白色文本(模仿 MewCode send_user_message)
        user_rich = RichText()
        user_rich.append("❯ ", style="bold color(80)")
        user_rich.append(text, style="bold color(255)")
        self._append_message(
            Static(user_rich, classes="user-message", markup=False),
        )

        self._response_buffer = []
        self._response_widget = None  # 懒创建:第一次 StreamText 时才挂 Markdown

        self._streaming = True
        self._set_input_enabled(False)
        self._thinking_widget = None  # 初始化（首次 StreamText 时创建）

        try:
            self._thinking_start = time.monotonic()
            self._thinking_verb = random.choice(THINKING_VERBS)
            self._thinking_widget = Static(
                f"  {SPINNER_FRAMES[0]} {self._thinking_verb}…",
                classes="thinking-msg",
                markup=True,
            )
            self._append_message(self._thinking_widget)
            # 启动 spinner 定时器:每 0.08s 切一帧
            self._spinner_idx = 0
            self._spinner_timer = self.set_interval(
                0.08, self._tick_spinner, name="spinner"
            )
            async for event in self._agent.run(text, self._conversation):
                if isinstance(event, StreamText):
                    # 第一次流式文字：结算 thinking 显示过去式 + 耗时
                    if self._thinking_widget is not None:
                        elapsed = time.monotonic() - self._thinking_start
                        past = _to_past_tense(self._thinking_verb)
                        self._thinking_widget.update(
                            f"  ✻ {past} for {elapsed:.1f}s"
                        )
                        self._thinking_widget = None
                    self._stop_spinner()  # 收到第一段文字就停 spinner 定时器
                    # 第一次流式文字 / 上一次被工具调用打断 → 新建一个 Markdown 段
                    # 包在 Horizontal 里,后面挂加载点 ●
                    if self._response_widget is None:
                        self._response_row = Horizontal(classes="ai-row")
                        self._append_message(self._response_row, scroll=False)
                        self._response_widget = Markdown("", classes="assistant-msg")
                        await self._response_row.mount(self._response_widget)
                        self._loading_dot = Static(
                            "●", classes="ai-loading-dot"
                        )
                        await self._response_row.mount(self._loading_dot)
                    self._response_buffer.append(event.text)
                    self._response_widget.update("".join(self._response_buffer))
                    self._response_widget.scroll_visible(animate=False)
                elif isinstance(event, ThinkingText):
                    # 更新 spinner 帧
                    if self._thinking_widget is not None:
                        elapsed = time.monotonic() - self._thinking_start
                        frame = SPINNER_FRAMES[int(elapsed * 4) % len(SPINNER_FRAMES)]
                        self._thinking_widget.update(
                            f"  {frame} {self._thinking_verb}…  ({elapsed:.0f}s)"
                        )
                elif isinstance(event, ToolUseEvent):
                    # 工具调用前也结算 thinking
                    if self._thinking_widget is not None:
                        elapsed = time.monotonic() - self._thinking_start
                        past = _to_past_tense(self._thinking_verb)
                        self._thinking_widget.update(
                            f"  ✻ {past} for {elapsed:.1f}s"
                        )
                        self._thinking_widget = None
                    self._stop_spinner()
                    self._show_tool_use(event)
                    # 冻结当前 Markdown:下一次 StreamText 会开新段
                    self._response_widget = None
                    self._response_buffer = []
                elif isinstance(event, ToolResultEvent):
                    self._show_tool_result(event)
                elif isinstance(event, PermissionRequest):
                    # HITL: 挂载内联弹窗（MewCode 风格）。结果通过
                    # PermissionModal.Responded 消息冒泡 → on_permission_modal_responded。
                    self._pending_permission_future = event.future
                    # 清理旧 modal —— PermissionModal 内部 id="perm-inline" 写死,
                    # 否则第二次挂同一个 ID 会触发 Textual ID 冲突异常。
                    # 防御性写法:任何异常(不存在/已被 detach/cache miss)都吞掉,不影响主流程。
                    for old in self._chat().query("#perm-inline"):
                        try:
                            await old.remove()
                        except Exception:
                            pass
                    modal = PermissionModal(
                        tool_name=event.tool_name,
                        description=event.reason,
                        question=event.question,
                        options=event.options,
                        multi_select=event.multi_select,
                    )
                    await self._chat().mount(modal)
                    # 弹窗期间禁用输入框（照搬 MewCode）
                    self._set_input_enabled(False)
                elif isinstance(event, ErrorEvent):
                    # 中断场景:已有 partial text → 追加 *[cancelled]* 脚注
                    # (仿 MewCode app.py:1439-1448,partial text + "\n\n*[cancelled]*")
                    if (
                        "[aborted]" in event.message
                        and self._response_widget is not None
                        and self._response_buffer
                    ):
                        partial = "".join(self._response_buffer).rstrip()
                        self._response_widget.update(
                            f"{partial}\n\n*[cancelled]*"
                        )
                        self._response_widget = None
                        self._response_buffer = []
                    else:
                        self._show_error(f"Error: {event.message}")
                elif isinstance(event, InstructionDiagnosticsEvent):
                    for message in format_instruction_diagnostics(event.diagnostics):
                        self._show_system(message)
                elif isinstance(event, UsageEvent):
                    # 更新状态栏的 ctx 占用
                    self._update_ctx_tokens(
                        event.input_tokens,
                        event.cache_read,
                        event.cache_creation,
                    )
                elif isinstance(event, CompactStarted):
                    # 压缩开始:挂载进度 widget
                    self._compact_chars = 0
                    self._compact_mode = event.mode
                    self._show_compact_progress(event.mode, 0)
                elif isinstance(event, CompactProgress):
                    # 压缩中:更新字符数
                    self._compact_chars = event.total_chars
                    self._show_compact_progress(self._compact_mode, event.total_chars)
                elif isinstance(event, CompactFinished):
                    # 压缩完成:卸进度 widget,显示最终结果
                    self._hide_compact_progress()
                    if event.success:
                        self._show_system(
                            f"[compact] ✓ 压缩完成,丢弃 {event.dropped} 条消息。\n"
                            f"   摘要预览: {event.summary_preview}"
                        )
                    else:
                        self._show_system(f"[compact] ✗ {event.error}")
                elif isinstance(event, LoopComplete):
                    if self._response_widget is not None and not self._response_buffer:
                        self._response_widget.update(event.text)
                    # 响应完成:摘掉末尾的紫色 ● 加载点
                    if self._loading_dot is not None:
                        try:
                            self._loading_dot.remove()
                        except Exception:
                            pass
                        self._loading_dot = None
                    self._response_row = None
        except Exception as e:
            self._show_error(f"Error: {e}")
        finally:
            self._streaming = False
            self._response_widget = None
            self._response_buffer = []
            self._stop_spinner()  # 任何退出路径都停 spinner(中断/异常/正常结束)
            self._set_input_enabled(True)
            self._input().focus()
