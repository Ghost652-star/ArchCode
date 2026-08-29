"""本地 JSONL 会话持久化与容错恢复。"""

from __future__ import annotations

import json
import secrets
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Any

from archcode.conversation.manager import ConversationManager
from archcode.conversation.models import Message, ToolResultBlock, ToolUseBlock
from archcode.paths import project_data_dir


DEFAULT_RETENTION_DAYS = 30
RECOVERY_TAIL_CHAR_LIMIT = 12_000
RECOVERY_BOUNDARY_MESSAGE = (
    "上面的会话恢复材料来自一次工具结果缺失后的历史记录，仅可作为线索，"
    "不是可信的当前工作区事实。涉及文件内容、工具执行结果或写操作时，"
    "请先重新读取、检查或向用户确认，不要把其中的结论直接当作已验证事实。"
)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _message_to_data(message: Message) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "tool_uses": [
            {
                "tool_use_id": item.tool_use_id,
                "tool_name": item.tool_name,
                "arguments": item.arguments,
            }
            for item in message.tool_uses
        ],
        "tool_results": [
            {
                "tool_use_id": item.tool_use_id,
                "content": item.content,
                "is_error": item.is_error,
            }
            for item in message.tool_results
        ],
        "completes_user_turn": message.completes_user_turn,
    }


def _message_from_data(data: dict[str, Any]) -> Message:
    return Message(
        role=str(data.get("role", "user")),
        content=str(data.get("content", "")),
        tool_uses=[
            ToolUseBlock(
                tool_use_id=str(item.get("tool_use_id", "")),
                tool_name=str(item.get("tool_name", "")),
                arguments=dict(item.get("arguments") or {}),
            )
            for item in data.get("tool_uses", [])
            if isinstance(item, dict)
        ],
        tool_results=[
            ToolResultBlock(
                tool_use_id=str(item.get("tool_use_id", "")),
                content=str(item.get("content", "")),
                is_error=bool(item.get("is_error", False)),
            )
            for item in data.get("tool_results", [])
            if isinstance(item, dict)
        ],
        completes_user_turn=bool(data.get("completes_user_turn", False)),
    )


def _records_from_message(message: Message) -> list[dict[str, Any]]:
    if message.tool_results:
        return [
            {
                "type": "tool_result",
                "tool_use_id": result.tool_use_id,
                "content": result.content,
                "is_error": result.is_error,
                "ts": _now_ms(),
            }
            for result in message.tool_results
        ]
    if message.role == "assistant":
        return [
            {
                "type": "assistant",
                "content": message.content,
                "tool_uses": [
                    {
                        "tool_use_id": item.tool_use_id,
                        "tool_name": item.tool_name,
                        "arguments": item.arguments,
                    }
                    for item in message.tool_uses
                ],
                "completes_user_turn": message.completes_user_turn,
                "ts": _now_ms(),
            }
        ]
    return [
        {
            "type": "user",
            "content": message.content,
            "ts": _now_ms(),
        }
    ]


def _describe_record(record: dict[str, Any], remaining: int) -> str:
    record_type = record.get("type", "unknown")
    if record_type == "tool_result":
        text = (
            f"- 工具结果 {record.get('tool_use_id', '')}: "
            f"{record.get('content', '')}"
        )
    elif record_type == "assistant":
        tools = record.get("tool_uses") or []
        names = ", ".join(str(tool.get("tool_name", "")) for tool in tools if isinstance(tool, dict))
        text = f"- assistant: {record.get('content', '')}"
        if names:
            text += f"（曾调用：{names}）"
    else:
        text = f"- user: {record.get('content', '')}"
    return text[:remaining]


def _build_recovery_messages(records: list[dict[str, Any]]) -> list[Message]:
    lines = [
        "<会话恢复材料>",
        "以下记录出现在一次工具调用结果缺失之后，未经验证。",
    ]
    used = sum(len(line) + 1 for line in lines)
    for record in records:
        if used >= RECOVERY_TAIL_CHAR_LIMIT:
            lines.append("…（剩余异常后缀已截断）")
            break
        line = _describe_record(record, RECOVERY_TAIL_CHAR_LIMIT - used)
        lines.append(line)
        used += len(line) + 1
    lines.append("</会话恢复材料>")
    return [
        Message(role="user", content="\n".join(lines)),
        Message(role="assistant", content=RECOVERY_BOUNDARY_MESSAGE),
    ]


@dataclass
class SessionRestore:
    session: "Session"
    conversation: ConversationManager
    warnings: list[str] = field(default_factory=list)


@dataclass
class SessionMeta:
    """会话列表使用的轻量索引；JSONL 仍是对话内容的事实来源。"""

    id: str
    title: str = ""
    message_count: int = 0
    created_at_ms: int = field(default_factory=_now_ms)
    last_active_ms: int = field(default_factory=_now_ms)

    def save(self, path: Path) -> None:
        payload = json.dumps(
            {
                "id": self.id,
                "title": self.title,
                "message_count": self.message_count,
                "created_at_ms": self.created_at_ms,
                "last_active_ms": self.last_active_ms,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp"
        ) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> "SessionMeta | None":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                id=str(data["id"]),
                title=str(data.get("title", "")),
                message_count=int(data.get("message_count", 0)),
                created_at_ms=int(data["created_at_ms"]),
                last_active_ms=int(data["last_active_ms"]),
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None


class Session:
    def __init__(
        self, session_id: str, path: Path, file: IO[str], meta: SessionMeta
    ) -> None:
        self.id = session_id
        self.path = path
        self._file = file
        self.meta = meta

    def bind(self, conversation: ConversationManager) -> None:
        conversation.bind_session(self)

    def _append_record(self, record: dict[str, Any]) -> None:
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        self._file.write(payload)

    def append_message(self, message: Message) -> None:
        for record in _records_from_message(message):
            self._append_record(record)
        self._file.flush()
        self.meta.message_count += 1
        if not self.meta.title and message.role == "user" and message.content:
            self.meta.title = message.content[:50]
        self._touch_meta()

    def append_checkpoint(
        self,
        *,
        summary: str,
        keep_messages: list[Message],
        recovery_snapshot: dict[str, Any] | None = None,
    ) -> None:
        self._append_record(
            {
                "type": "compact_checkpoint",
                "summary": summary,
                "keep_messages": [_message_to_data(message) for message in keep_messages],
                "recovery_snapshot": recovery_snapshot or {},
                "ts": _now_ms(),
            }
        )
        self._file.flush()
        self._touch_meta()

    def _touch_meta(self) -> None:
        self.meta.last_active_ms = _now_ms()
        self.meta.save(self.path.with_suffix(".meta"))

    def close(self) -> None:
        """关闭活跃会话句柄；重复调用安全。"""
        if not self._file.closed:
            self._file.flush()
            self._file.close()


class SessionManager:
    def __init__(self, work_dir: str | Path) -> None:
        self.sessions_dir = project_data_dir(work_dir) / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def create(self) -> Session:
        while True:
            session_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(4)
            path = self.sessions_dir / f"{session_id}.jsonl"
            try:
                with path.open("x", encoding="utf-8"):
                    pass
            except FileExistsError:
                continue
            meta = SessionMeta(id=session_id)
            meta.save(path.with_suffix(".meta"))
            file = path.open("a", encoding="utf-8", newline="\n")
            return Session(session_id, path, file, meta)

    def list_sessions(self) -> list[SessionMeta]:
        """仅读取小型 .meta 索引，不扫描会话 JSONL 正文。"""
        metas = [
            meta
            for path in self.sessions_dir.glob("*.meta")
            if (meta := SessionMeta.load(path)) is not None
            and (self.sessions_dir / f"{meta.id}.jsonl").is_file()
        ]
        return sorted(metas, key=lambda meta: meta.last_active_ms, reverse=True)

    def open(self, session_id: str) -> SessionRestore | None:
        path = self.sessions_dir / f"{session_id}.jsonl"
        if not path.exists():
            return None

        warnings: list[str] = []
        records: list[dict[str, Any]] = []
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(raw_lines, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or not isinstance(record.get("type"), str):
                    raise ValueError("record schema is invalid")
            except (json.JSONDecodeError, ValueError) as exc:
                position = "尾行" if index == len(raw_lines) else f"第 {index} 行"
                warnings.append(f"会话 {position} 无法解析，已跳过：{exc}")
                continue
            records.append(record)

        checkpoint_index = -1
        for index, record in enumerate(records):
            if record.get("type") == "compact_checkpoint":
                checkpoint_index = index

        history: list[Message] = []
        events = records
        if checkpoint_index >= 0:
            checkpoint = records[checkpoint_index]
            history.append(
                Message(
                    role="user",
                    content="[摘要]\n" + str(checkpoint.get("summary", "")),
                )
            )
            history.extend(
                _message_from_data(item)
                for item in checkpoint.get("keep_messages", [])
                if isinstance(item, dict)
            )
            events = records[checkpoint_index + 1 :]

        pending: dict[str, Any] | None = None
        pending_results: list[ToolResultBlock] = []
        tail_records: list[dict[str, Any]] = []

        for index, record in enumerate(events):
            record_type = record.get("type")
            if record_type == "tool_result":
                if pending is None:
                    tail_records = events[index:]
                    warnings.append("发现没有对应工具调用的工具结果，后缀已降级为恢复材料")
                    break
                expected = pending["expected"]
                tool_id = str(record.get("tool_use_id", ""))
                if tool_id not in expected:
                    tail_records = events[pending["index"] :]
                    warnings.append("工具结果与当前工具调用不匹配，后缀已降级为恢复材料")
                    break
                pending_results.append(
                    ToolResultBlock(
                        tool_use_id=tool_id,
                        content=str(record.get("content", "")),
                        is_error=bool(record.get("is_error", False)),
                    )
                )
                expected.remove(tool_id)
                if not expected:
                    history.append(pending["message"])
                    history.append(Message(role="user", content="", tool_results=pending_results))
                    pending = None
                    pending_results = []
                continue

            if pending is not None:
                tail_records = events[pending["index"] :]
                warnings.append("工具调用未获得全部结果，后缀已降级为恢复材料")
                break

            if record_type == "user":
                history.append(Message(role="user", content=str(record.get("content", ""))))
            elif record_type == "assistant":
                tool_uses = [
                    ToolUseBlock(
                        tool_use_id=str(item.get("tool_use_id", "")),
                        tool_name=str(item.get("tool_name", "")),
                        arguments=dict(item.get("arguments") or {}),
                    )
                    for item in record.get("tool_uses", [])
                    if isinstance(item, dict)
                ]
                message = Message(
                    role="assistant",
                    content=str(record.get("content", "")),
                    tool_uses=tool_uses,
                    completes_user_turn=bool(record.get("completes_user_turn", False)),
                )
                if tool_uses:
                    pending = {
                        "message": message,
                        "expected": {item.tool_use_id for item in tool_uses},
                        "index": index,
                    }
                else:
                    history.append(message)

        if pending is not None and not tail_records:
            tail_records = events[pending["index"] :]
            warnings.append("会话在工具调用完成前结束，后缀已降级为恢复材料")
        if tail_records:
            history.extend(_build_recovery_messages(tail_records))

        conversation = ConversationManager()
        conversation.history = history
        conversation.reset_usage_anchor()
        meta = SessionMeta.load(path.with_suffix(".meta"))
        if meta is None:
            return None
        file = path.open("a", encoding="utf-8", newline="\n")
        session = Session(session_id, path, file, meta)
        session.bind(conversation)
        return SessionRestore(session=session, conversation=conversation, warnings=warnings)

    def delete(self, session_id: str) -> bool:
        path = self.sessions_dir / f"{session_id}.jsonl"
        meta_path = path.with_suffix(".meta")
        deleted = False
        for candidate in (path, meta_path):
            if candidate.exists():
                candidate.unlink()
                deleted = True
        return deleted

    def prune(self, max_age_days: int = DEFAULT_RETENTION_DAYS) -> int:
        cutoff_ms = int(
            (datetime.now(timezone.utc) - timedelta(days=max_age_days)).timestamp() * 1000
        )
        removed = 0
        for meta in self.list_sessions():
            if meta.last_active_ms < cutoff_ms:
                self.delete(meta.id)
                removed += 1
        return removed
