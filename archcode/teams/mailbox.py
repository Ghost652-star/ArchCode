"""文件邮箱(agent-teams-design §4/§5.4):队员间消息的持久化通道。

每队员一个收件箱目录,一条消息一个 json 文件(追加语义,读即消费)。
写入三道关:格式校验(写时校验,坏消息不落盘)→ 收件箱锁(O_CREATE|O_EXCL 抢锁 +
抖动重试 + stale 接管)→ 原子落盘。in-process 后端下协程并发也由文件锁串行,
不需要额外的内存锁。
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

from archcode.teams.models import MailboxMessage

logger = logging.getLogger(__name__)

_LOCK_RETRY_MAX = 10
_LOCK_RETRY_MIN_MS = 5
_LOCK_RETRY_MAX_MS = 100
_LOCK_STALE_SECONDS = 10
_MAX_CONTENT_CHARS = 20000
_MAX_SUMMARY_CHARS = 60
_MESSAGE_TYPES = {"text", "shutdown_request", "shutdown_response", "plan_approval_response"}


class TeamMailbox:
    """一个团队的全部收件箱(每个队员一个子目录,消息一条一文件)。"""

    def __init__(self, team_dir: str | Path) -> None:
        self._base = Path(team_dir) / "mailbox"

    def inbox_dir(self, agent_name: str) -> Path:
        return self._base / agent_name

    # ------------------------------------------------------------------
    # 写入:写时校验 → 锁 → 原子落盘
    # ------------------------------------------------------------------

    def write(self, msg: MailboxMessage) -> None:
        self._validate(msg)
        inbox = self.inbox_dir(msg.to_name)
        inbox.mkdir(parents=True, exist_ok=True)
        target = inbox / f"msg-{msg.msg_id}.json"
        with self._lock(inbox):
            if target.exists():  # msg_id 唯一(重启重算),重复写视为幂等
                return
            payload = json.dumps(
                {
                    "msg_id": msg.msg_id,
                    "from": msg.from_name,
                    "to": msg.to_name,
                    "type": msg.message_type,
                    "content": msg.content,
                    "summary": msg.summary,
                    "created_at": msg.created_at,
                },
                ensure_ascii=False,
                indent=2,
            )
            tmp = target.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(target)

    @staticmethod
    def _validate(msg: MailboxMessage) -> None:
        if msg.message_type not in _MESSAGE_TYPES:
            raise ValueError(f"未知消息类型: {msg.message_type!r}")
        if msg.message_type == "text":
            if not msg.summary.strip():
                raise ValueError("text 消息必须提供 summary(5-10 词预览)")
            if len(msg.summary) > _MAX_SUMMARY_CHARS:
                raise ValueError(f"summary 过长(>{_MAX_SUMMARY_CHARS} 字符)")
        if len(msg.content) > _MAX_CONTENT_CHARS:
            raise ValueError(f"消息正文过长(>{_MAX_CONTENT_CHARS} 字符)")

    # ------------------------------------------------------------------
    # 读取:读即消费(drain)
    # ------------------------------------------------------------------

    def drain(self, agent_name: str) -> list[MailboxMessage]:
        inbox = self.inbox_dir(agent_name)
        if not inbox.exists():
            return []
        msgs: list[MailboxMessage] = []
        with self._lock(inbox):
            for f in sorted(inbox.glob("msg-*.json")):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning("坏消息文件移除: %s (%s)", f.name, e)
                    f.unlink(missing_ok=True)
                    continue
                msgs.append(
                    MailboxMessage(
                        msg_id=str(data.get("msg_id", "")),
                        from_name=str(data.get("from", "")),
                        to_name=str(data.get("to", "")),
                        message_type=data.get("type", "text"),
                        content=str(data.get("content", "")),
                        summary=str(data.get("summary", "")),
                        created_at=str(data.get("created_at", "")),
                    )
                )
                f.unlink(missing_ok=True)  # 读即消费
        return msgs

    def clear(self) -> None:
        """TeamDelete 清理:删整个 mailbox 子树。"""
        import shutil

        if self._base.exists():
            shutil.rmtree(self._base, ignore_errors=True)

    # ------------------------------------------------------------------
    # 收件箱锁(O_CREATE|O_EXCL 原子创建即获锁)
    # ------------------------------------------------------------------

    class _Locked:
        """锁上下文:退出时释放。"""

        def __init__(self, lock_path: Path) -> None:
            self._path = lock_path

        def __enter__(self) -> "TeamMailbox._Locked":
            return self

        def __exit__(self, *exc: Any) -> None:
            self._path.unlink(missing_ok=True)

    def _lock(self, inbox: Path) -> "TeamMailbox._Locked":
        lock = inbox / ".lock"
        inbox.mkdir(parents=True, exist_ok=True)
        for attempt in range(_LOCK_RETRY_MAX):
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return TeamMailbox._Locked(lock)
            except FileExistsError:
                # stale 接管:超时未释放的锁直接抢掉(防崩溃残留死锁)
                try:
                    if time.time() - lock.stat().st_mtime > _LOCK_STALE_SECONDS:
                        lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    continue
                time.sleep(random.uniform(_LOCK_RETRY_MIN_MS, _LOCK_RETRY_MAX_MS) / 1000)
        # 重试耗尽:视为 stale 强制接管(与 10s 超时同语义的最后兜底)
        lock.unlink(missing_ok=True)
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return TeamMailbox._Locked(lock)
