from __future__ import annotations

import asyncio
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
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
)
from archcode.conversation.manager import ConversationManager


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

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="chat")
        yield ChatInput(
            id="input",
            placeholder="输入消息，Enter 发送，Shift+Enter 换行，/clear 清空对话",
        )
        yield Static(
            f"模型: {self._model_name}  |  Ctrl+L 清空  Ctrl+C 退出",
            classes="status-bar",
        )
        yield Footer()

    def _chat(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    def _input(self) -> ChatInput:
        return self.query_one("#input", ChatInput)

    def _set_input_enabled(self, enabled: bool) -> None:
        self._input().disabled = not enabled

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

        if self._streaming:
            return

        await self._handle_user_message(text)

    async def _handle_user_message(self, text: str) -> None:
        self._append_message(
            Static(f"[bold]You[/bold]\n{text}", classes="user-msg"),
        )

        self._response_buffer = []
        self._response_widget = None  # 懒创建:第一次 StreamText 时才挂 Markdown

        self._streaming = True
        self._set_input_enabled(False)

        try:
            async for event in self._agent.run(text, self._conversation):
                if isinstance(event, StreamText):
                    # 第一次流式文字 / 上一次被工具调用打断 → 新建一个 Markdown 段
                    if self._response_widget is None:
                        self._response_widget = Markdown("", classes="assistant-msg")
                        self._append_message(self._response_widget, scroll=False)
                    self._response_buffer.append(event.text)
                    self._response_widget.update("".join(self._response_buffer))
                    self._response_widget.scroll_visible(animate=False)
                elif isinstance(event, ThinkingText):
                    self._show_thinking(event.text)
                elif isinstance(event, ToolUseEvent):
                    self._show_tool_use(event)
                    # 冻结当前 Markdown:下一次 StreamText 会开新段
                    self._response_widget = None
                    self._response_buffer = []
                elif isinstance(event, ToolResultEvent):
                    self._show_tool_result(event)
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
