from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from archcode.conversation.models import (
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    estimate_tokens,
)

if TYPE_CHECKING:
    from archcode.memory.session import Session


@dataclass
class ConversationManager:
    """对话历史管理器。"""

    history: list[Message] = field(default_factory=list)
    baseline_tokens: int = field(default=0, init=False)
    anchor_count: int = field(default=0, init=False)
    _session: "Session | None" = field(default=None, init=False, repr=False)

    def bind_session(self, session: "Session") -> None:
        """绑定会话后，普通对话消息先写盘再进入内存。"""
        self._session = session

    def _append_persisted(self, message: Message) -> None:
        if self._session is not None:
            self._session.append_message(message)
        self.history.append(message)

    def get_messages(self) -> list[Message]:
        return list(self.history)

    def add_user(self, content: str) -> None:
        self._append_persisted(Message(role="user", content=content))

    def add_assistant(
        self,
        content: str,
        *,
        tool_uses: list[ToolUseBlock] | None = None,
        thinking_blocks: list[ThinkingBlock] | None = None,
        completes_user_turn: bool = False,
    ) -> None:
        self._append_persisted(
            Message(
                role="assistant",
                content=content,
                tool_uses=tool_uses or [],
                thinking_blocks=thinking_blocks or [],
                completes_user_turn=completes_user_turn,
            )
        )

    def add_tool_results(self, tool_results: list[ToolResultBlock]) -> None:
        self._append_persisted(
            Message(role="user", content="", tool_results=tool_results)
        )

    def add_assistant_message(
        self,
        content: str,
        *,
        tool_uses: list[ToolUseBlock] | None = None,
        thinking_blocks: list[ThinkingBlock] | None = None,
        completes_user_turn: bool = False,
    ) -> None:
        """add_assistant 的别名，供 agent 直接调用。"""
        self.add_assistant(
            content,
            tool_uses=tool_uses,
            thinking_blocks=thinking_blocks,
            completes_user_turn=completes_user_turn,
        )

    def add_tool_results_message(self, tool_results: list[ToolResultBlock]) -> None:
        """add_tool_results 的别名，供 agent 直接调用。"""
        self.add_tool_results(tool_results)

    def add_system_reminder(self, content: str) -> None:
        """注入一条 system-reminder 消息。

        用 role=user 但 content
        用 ``<system-reminder>`` 标签包起来 — Anthropic SDK 把这种
        格式当作高优先级 system 指令处理,等价于 system prompt。

        也可以避免走 Anthropic API 的 top-level ``system`` 字段
        (那个字段在 streaming 模式下不能动态注入,只能初次调用时设)。
        """
        self.history.append(
            Message(
                role="user",
                content=f"<system-reminder>\n{content}\n</system-reminder>",
            )
        )

    def inject_environment(self, env_context: str) -> None:
        """在历史中查找或插入环境上下文消息。"""
        # 简单策略：追加一条 system 消息。更完善的实现可做去重。
        self.history.append(Message(role="system", content=env_context))

    def inject_long_term_memory(
        self, instructions: str, memory_content: str
    ) -> None:
        if instructions or memory_content:
            parts = []
            if instructions:
                parts.append(instructions)
            if memory_content:
                parts.append(f"## Long-term Memory\n{memory_content}")
            self.history.append(Message(role="system", content="\n\n".join(parts)))

    def clear(self) -> None:
        self.history.clear()
        self.baseline_tokens = 0
        self.anchor_count = 0

    def reset_usage_anchor(self) -> None:
        self.baseline_tokens = 0
        self.anchor_count = 0

    def persist_compact_checkpoint(
        self, summary: str, keep_messages: list[Message]
    ) -> None:
        """将已成功压缩的会话事实写成恢复 checkpoint。"""
        if self._session is not None:
            self._session.append_checkpoint(
                summary=summary,
                keep_messages=keep_messages,
            )

    def replace_history(self, new_messages: list[Message]) -> None:
        """原子替换整个 history,清空 token 锚点。

        压缩模块用:把「summary user 消息 + keep_tail」一次性塞进来,清空
        ``baseline_tokens`` 和 ``anchor_count``,否则下次 ``current_tokens()``
        会把旧 anchor 之后的字符估算叠加在新 history 上 —— double-counting。

        下一次 ``record_usage_anchor`` 会基于新 history 重新锚定。
        """
        self.history = list(new_messages)
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
