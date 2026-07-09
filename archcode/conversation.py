from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system"
    content: str


@dataclass
class ConversationManager:
    """对话历史管理。后续可扩展 tool_uses / tool_results / thinking_blocks。"""

    history: list[Message] = field(default_factory=list)

    def add_user(self, content: str) -> None:
        self.history.append(Message(role="user", content=content))

    def add_assistant(self, content: str) -> None:
        self.history.append(Message(role="assistant", content=content))

    def clear(self) -> None:
        self.history.clear()

    def to_api_messages(self, system_prompt: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for msg in self.history:
            messages.append({"role": msg.role, "content": msg.content})
        return messages
