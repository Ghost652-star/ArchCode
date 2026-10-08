"""队员对话持久化(agent-teams-design §8.2):空闲/中止后可从磁盘恢复续写。

队员 conversation 全量落盘(按 agent_id 一份);恢复 = 载入记录重建 Agent 实例,
与 session 恢复(resume)同一套语义。粒度定稿为全量(压缩留作后续优化)。

落盘时机(2026-10-08 接线定稿):每次 run 结束(空闲或中止)在 _TeammateRunner 的
finally 里存检查点——恢复的语义是"带着已完成的工作 + 追加的新指令继续",
未跑完的半截对话本就不该恢复;要"随对话推进落盘"可再加增量钩子。

消息格式与 session JSONL 的 _message_to_data 同构(role/content/tool_uses/
tool_results/thinking_blocks/completes_user_turn),恢复后可直接进 ConversationManager。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from archcode.conversation.models import (
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)


def transcript_dir(team_dir: str | Path, agent_id: str) -> Path:
    return Path(team_dir) / "transcripts" / agent_id


def messages_to_data(history: list[Message]) -> list[dict[str, Any]]:
    return [
        {
            "role": m.role,
            "content": m.content,
            "tool_uses": [
                {
                    "tool_use_id": tu.tool_use_id,
                    "tool_name": tu.tool_name,
                    "arguments": tu.arguments,
                }
                for tu in m.tool_uses
            ],
            "tool_results": [
                {
                    "tool_use_id": tr.tool_use_id,
                    "content": tr.content,
                    "is_error": tr.is_error,
                }
                for tr in m.tool_results
            ],
            "thinking_blocks": [
                {"thinking": tb.thinking, "signature": tb.signature}
                for tb in m.thinking_blocks
            ],
            "completes_user_turn": m.completes_user_turn,
        }
        for m in history
    ]


def messages_from_data(data: Any) -> list[Message]:
    if not isinstance(data, list):
        return []
    out: list[Message] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        out.append(
            Message(
                role=str(entry.get("role", "user")),
                content=str(entry.get("content", "")),
                tool_uses=[
                    ToolUseBlock(
                        tool_use_id=str(item.get("tool_use_id", "")),
                        tool_name=str(item.get("tool_name", "")),
                        arguments=dict(item.get("arguments") or {}),
                    )
                    for item in entry.get("tool_uses", [])
                    if isinstance(item, dict)
                ],
                tool_results=[
                    ToolResultBlock(
                        tool_use_id=str(item.get("tool_use_id", "")),
                        content=str(item.get("content", "")),
                        is_error=bool(item.get("is_error", False)),
                    )
                    for item in entry.get("tool_results", [])
                    if isinstance(item, dict)
                ],
                thinking_blocks=[
                    ThinkingBlock(
                        thinking=str(item.get("thinking", "")),
                        signature=str(item.get("signature", "")),
                    )
                    for item in entry.get("thinking_blocks", [])
                    if isinstance(item, dict)
                ],
                completes_user_turn=bool(entry.get("completes_user_turn", False)),
            )
        )
    return out


def save_transcript(team_dir: str | Path, agent_id: str, history: list[Message]) -> None:
    d = transcript_dir(team_dir, agent_id)
    d.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(messages_to_data(history), ensure_ascii=False, indent=2)
    tmp = d / "messages.json.tmp"
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(d / "messages.json")


def load_transcript(team_dir: str | Path, agent_id: str) -> list[Message] | None:
    p = transcript_dir(team_dir, agent_id) / "messages.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("transcript 读取失败(%s): %s", agent_id, e)
        return None
    msgs = messages_from_data(data)
    return msgs or None
