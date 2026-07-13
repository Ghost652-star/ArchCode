from __future__ import annotations

import asyncio
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message as TMessage
from textual.widgets import Footer, Header, Markdown, Static, TextArea

from archcode.agent import Agent, ErrorEvent, LoopComplete, StreamText
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

    def action_clear_chat(self) -> None:
        self._conversation.clear()
        self._chat().remove_children()
        self._show_system("对话已清空。")

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

        if self._streaming:
            return

        await self._handle_user_message(text)

    async def _handle_user_message(self, text: str) -> None:
        self._append_message(
            Static(f"[bold]You[/bold]\n{text}", classes="user-msg"),
        )

        self._response_buffer = []
        self._response_widget = Markdown("", classes="assistant-msg")
        self._append_message(self._response_widget, scroll=False)

        self._streaming = True
        self._set_input_enabled(False)

        try:
            async for event in self._agent.run(text, self._conversation):
                if isinstance(event, StreamText):
                    self._response_buffer.append(event.text)
                    if self._response_widget is not None:
                        self._response_widget.update("".join(self._response_buffer))
                        self._response_widget.scroll_visible(animate=False)
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
            self._set_input_enabled(True)
            self._input().focus()
