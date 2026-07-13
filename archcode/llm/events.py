from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TextDelta:
    """助手可见文本的增量片段。UI 直接拼接显示。"""

    text: str


@dataclass
class ThinkingDelta:
    """模型思考过程的增量。一般可折叠展示，不进最终回复。"""

    text: str


@dataclass
class ThinkingComplete:
    """一整段思考结束，附带完整内容与签名。"""

    thinking: str
    signature: str = ""


@dataclass
class ToolCallStart:
    """开始一次工具调用。此时参数仍在流式到达。"""

    tool_name: str
    tool_id: str


@dataclass
class ToolCallDelta:
    """工具参数 JSON 的增量片段。"""

    text: str


@dataclass
class ToolCallComplete:
    """工具调用参数收齐，可执行。"""

    tool_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class StreamEnd:
    """本轮 LLM 流结束。携带停止原因与 token 用量。"""

    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0


StreamEvent = (
    TextDelta
    | ThinkingDelta
    | ThinkingComplete
    | ToolCallStart
    | ToolCallDelta
    | ToolCallComplete
    | StreamEnd
)
