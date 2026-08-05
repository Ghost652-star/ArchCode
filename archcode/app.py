from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message as TMessage
from textual.widgets import Footer, Header, Markdown, Static, TextArea

from archcode.agent import (
    Agent,
    ErrorEvent,
    LoopComplete,
    PermissionRequest,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
)
from archcode.conversation.manager import ConversationManager
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
    ]

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
        self._streaming = False
        self._agent_task: asyncio.Task[None] | None = None
        self._response_widget: Markdown | None = None
        self._response_buffer: list[str] = []
        self._pending_permission_future: asyncio.Future | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="chat")
        yield ChatInput(
            id="input",
            placeholder="输入消息，Enter 发送，Shift+Enter 换行",
        )
        yield Static(
            self._status_bar_text(),
            classes="status-bar",
            id="status-bar",
        )

    def _status_bar_text(self) -> str:
        mode_label = self._get_mode_label()
        work_label = self._get_work_dir_label()
        return f"[{self._model_name}]  {work_label}  权限: {mode_label}"

    def _get_work_dir_label(self) -> str:
        """从 agent 读当前工作目录,显示在状态栏。"""
        try:
            wd = getattr(self._agent, "_work_dir", None)
            if wd:
                return f"📁 {Path(wd)}"
        except Exception:
            pass
        return "📁 (cwd)"

    def _get_mode_label(self) -> str:
        """从 agent 的 permission_checker 读取当前模式的名字。"""
        try:
            checker = getattr(self._agent, "_permission_checker", None)
            if checker is not None:
                return checker.mode.value
        except Exception:
            pass
        return "default"

    def _update_status_bar(self) -> None:
        try:
            sb = self.query_one("#status-bar", Static)
            sb.update(self._status_bar_text())
        except Exception:
            pass

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
            self.query_one("#perm-inline", PermissionModal).remove()
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

    def _show_tool_use(self, event: ToolUseEvent) -> None:
        """显示工具调用:工具名 + 参数。"""
        args_str = ", ".join(f"{k}={v!r}" for k, v in event.arguments.items())
        self._append_message(
            Static(f"⚙ [tool]{event.tool_name}[/tool]({args_str})", classes="tool-use-msg")
        )

    def _show_tool_result(self, event: ToolResultEvent) -> None:
        """显示工具结果:仅摘要(名称 + 耗时 + 字节数),不展示实际 output
        — output 已被 LLM 消费,UI 重复展示会刷屏。"""
        elapsed_ms = event.elapsed * 1000
        status = "✗" if event.is_error else "✓"
        classes = "tool-result-msg" if not event.is_error else "tool-result-msg-error"
        char_count = len(event.output)
        first_line = event.output.splitlines()[0][:80] if event.output else ""
        self._append_message(
            Static(
                f"{status} [{event.tool_name}] ({elapsed_ms:.1f}ms, {char_count:,} chars)"
                + (f"\n   {first_line}" if first_line and not event.is_error else ""),
                classes=classes,
                markup=False,
            )
        )

    def _show_thinking(self, text: str) -> None:
        """显示模型思考文本:斜体灰色,与正文视觉区分。"""
        self._append_message(Static(text, classes="thinking-msg", markup=False))

    def action_clear_chat(self) -> None:
        self._conversation.clear()
        self._chat().remove_children()
        self._show_system("对话已清空。")

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
        self._show_system(f"权限模式已切换为: {mode.value}")
        self._update_status_bar()

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
                    f"  当前模式: {self._get_mode_label()}"
                )
            return

        if self._streaming:
            return

        # 照搬 MewCode（app.py:941）：agent 循环放独立 task，事件处理器立即返回。
        # 若在 handler 里 await 整个 agent 运行，HITL 等待期间 handler 一直挂起，
        # Textual 消息泵会阻塞，弹窗按键失去响应（设计文档 §8.7）。
        self._agent_task = asyncio.create_task(
            self._handle_user_message(text)
        )

    async def _handle_user_message(self, text: str) -> None:
        self._append_message(
            Static(f"[bold]You[/bold]\n{text}", classes="user-msg"),
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
                    # 第一次流式文字 / 上一次被工具调用打断 → 新建一个 Markdown 段
                    if self._response_widget is None:
                        self._response_widget = Markdown("", classes="assistant-msg")
                        self._append_message(self._response_widget, scroll=False)
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
                    self._show_error(f"Error: {event.message}")
                elif isinstance(event, LoopComplete):
                    if self._response_widget is not None and not self._response_buffer:
                        self._response_widget.update(event.text)
        except Exception as e:
            self._show_error(f"Error: {e}")
        finally:
            self._streaming = False
            self._response_widget = None
            self._response_buffer = []
            self._set_input_enabled(True)
            self._input().focus()
