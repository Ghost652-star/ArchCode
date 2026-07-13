from __future__ import annotations

from dataclasses import dataclass, field

from archcode.conversation.models import (
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    estimate_tokens,
)


@dataclass
class ConversationManager:
    """对话历史管理器。"""

    history: list[Message] = field(default_factory=list)
    baseline_tokens: int = field(default=0, init=False)
    anchor_count: int = field(default=0, init=False)

    def get_messages(self) -> list[Message]:
        return list(self.history)

    def add_user(self, content: str) -> None:
        self.history.append(Message(role="user", content=content))

    def add_assistant(
        self,
        content: str,
        *,
        tool_uses: list[ToolUseBlock] | None = None,
        thinking_blocks: list[ThinkingBlock] | None = None,
    ) -> None:
        self.history.append(
            Message(
                role="assistant",
                content=content,
                tool_uses=tool_uses or [],
                thinking_blocks=thinking_blocks or [],
            )
        )

    def add_tool_results(self, tool_results: list[ToolResultBlock]) -> None:
        self.history.append(
            Message(role="user", content="", tool_results=tool_results)
        )

    def clear(self) -> None:
        self.history.clear()
        self.baseline_tokens = 0
        self.anchor_count = 0

    def record_usage_anchor(
        self,
        input_tokens: int,
        output_tokens: int = 0,
        cache_read: int = 0,
        cache_creation: int = 0,
    ) -> None:
        self.baseline_tokens = (
            input_tokens + cache_read + cache_creation + output_tokens
        )
        self.anchor_count = len(self.history)

    def current_tokens(self) -> int:
        if self.baseline_tokens <= 0:
            return estimate_tokens(self.history)
        return self.baseline_tokens + estimate_tokens(self.history[self.anchor_count :])
