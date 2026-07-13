from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolUseBlock:
    tool_use_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass
class ThinkingBlock:
    thinking: str
    signature: str = ""


@dataclass
class Message:
    """协议无关的内部消息。序列化层负责转成各家 API 格式。"""

    role: str
    content: str
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    tool_results: list[ToolResultBlock] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)


_CHARS_PER_TOKEN = 3.5


def estimate_tokens(messages: list[Message]) -> int:
    total = 0
    for message in messages:
        total += len(message.content)
        for thinking_block in message.thinking_blocks:
            total += len(thinking_block.thinking)
        for tool_use in message.tool_uses:
            total += len(tool_use.tool_name) + len(
                json.dumps(tool_use.arguments, ensure_ascii=False)
            )
        for tool_result in message.tool_results:
            total += len(tool_result.content)
    return int(total / _CHARS_PER_TOKEN)
