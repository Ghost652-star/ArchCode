"""文件邮箱(agent-teams-design §5.4,2026-10-08 修订为无锁):队员间消息的持久化通道。

每队员一个收件箱目录,一条消息一个 json 文件(追加语义,读即消费)。

**无锁的根据**(§5.4 修订):写方互斥的前提不存在——in-process 后端 (§4.3.5) 下
所有写都跑在同一事件循环上,write/drain 临界区无 await,协程间天然串行;跨进程
写方(Pane 后端)已裁,锁要防的那种竞争没有写方。消息完整性由三条保证:
①文件名由内部生成的 msg_id 决定(非外部输入,撞名概率为零);②写走唯一临时文件
+ rename 原子发布(drain 永远读不到半条);③drain 解析失败即移除,不把损坏内容
当消息消费。写时校验(坏消息不落盘)保留。

若将来接回跨进程后端,必须重新引入锁并遵守 §4.3.3 的两条原则(一次性前置 +
不静默降级)——不要在无写方互斥的前提下预先持锁。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from archcode.teams.models import MailboxMessage

logger = logging.getLogger(__name__)

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
    # 写入:写时校验 → 唯一临时文件 → 原子 rename
    # ------------------------------------------------------------------

    def write(self, msg: MailboxMessage) -> None:
        self._validate(msg)
        inbox = self.inbox_dir(msg.to_name)
        inbox.mkdir(parents=True, exist_ok=True)
        target = inbox / f"msg-{msg.msg_id}.json"
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
        # 临时名带 pid + 随机后缀:即使将来出现第二个写方(线程/进程),
        # 并发写也落在不同临时文件上,不靠锁串行
        tmp = inbox / f".tmp-{msg.msg_id}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)

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
    # 读取:读即消费(drain);坏文件移除(不把损坏内容当消息)
    # ------------------------------------------------------------------

    def drain(self, agent_name: str) -> list[MailboxMessage]:
        inbox = self.inbox_dir(agent_name)
        if not inbox.exists():
            return []
        msgs: list[MailboxMessage] = []
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
