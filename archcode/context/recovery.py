"""RecoveryState:压缩后给模型看的「恢复上下文」。

记录这一会话里 LLM 读过哪些文件 / 激活过哪些 skills。
压缩触发后,把近期文件 + skills + 当前可用工具列表拼成一段 Markdown,
作为摘要消息的 boundary attachment —— 让模型知道这些内容仍存在,
需要原文用 ``ReadFile`` / ``Skill`` 重新读取。

设计参照 MewCode 的 ``RecoveryState``,但 ArchCode 的 ``skills/`` 是空包,
所以小节 (2) 自然 skip。接口仍完整保留,等 skills 子系统上线时直接接。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass
class FileReadRecord:
    path: str
    content: str
    timestamp: float


@dataclass
class SkillInvocationRecord:
    name: str
    body: str
    timestamp: float


class RecoveryState:
    """线程安全的文件 / skill 记录器。

    agent loop 和工具执行可能在不同 task 中并发(尤其 MCP 工具走异步),
    因此读写都要加锁。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # path → 最近一次记录(同一文件多次读,只保留最新)
        self._files: dict[str, FileReadRecord] = {}
        # skill_name → 最近一次记录
        self._skills: dict[str, SkillInvocationRecord] = {}

    # ── 写入 ────────────────────────────────────────────────────────

    def record_file_read(self, path: str, content: str) -> None:
        """记录一次文件读取。同 path 会覆盖。"""
        with self._lock:
            self._files[path] = FileReadRecord(
                path=path,
                content=content,
                timestamp=time.time(),
            )

    def record_skill_invocation(self, name: str, body: str) -> None:
        """记录一次 skill 激活。ArchCode 当前无调用方,接口预留。

        TODO(skills):由 SkillExecutor 的 inline / fork 执行路径调用。``body``
        应为参数替换后的实际指令；SkillInvocationRecord 后续应扩展执行模式与
        可选 child task / conversation id，避免压缩后只知道模板而不知道实际目标。
        """
        with self._lock:
            self._skills[name] = SkillInvocationRecord(
                name=name,
                body=body,
                timestamp=time.time(),
            )

    # ── 读取 ────────────────────────────────────────────────────────

    def snapshot_files(self, limit: int) -> list[FileReadRecord]:
        """返回最近 ``limit`` 条文件记录,按时间倒序。"""
        with self._lock:
            items = sorted(
                self._files.values(), key=lambda r: r.timestamp, reverse=True
            )
        return items[:limit]

    def snapshot_skills(self) -> list[SkillInvocationRecord]:
        """返回所有 skills 快照,按时间倒序。"""
        with self._lock:
            items = sorted(
                self._skills.values(), key=lambda r: r.timestamp, reverse=True
            )
        return list(items)


def _format_timestamp(ts: float) -> str:
    """把 epoch 秒渲染成可读时间字符串,失败返回 ISO。"""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (OSError, ValueError):
        return f"<ts={ts}>"


def _approx_tokens(s: str) -> int:
    """粗估字符数 → token 数(跟 ``estimate_tokens`` 同一个 _CHARS_PER_TOKEN=3.5)。

    放这里避免从 ``conversation.models`` import(避免循环依赖)。
    """
    return int(len(s) / 3.5)


def build_recovery_attachment(
    state: RecoveryState | None,
    tool_schemas: list[Mapping[str, Any]] | None,
    *,
    file_limit: int = 5,
    tokens_per_file: int = 5_000,
    skills_budget: int = 25_000,
    tokens_per_skill: int = 5_000,
) -> str:
    """渲染 4 小节的 Markdown 附件:files / skills / tools / hint。

    任何小节无内容就跳过该小节。返回 ``""`` 表示整个附件为空,
    调用方保持摘要消息干净。

    Args:
        state: ``RecoveryState`` 实例;为 None 时跳过 files / skills 小节
        tool_schemas:当前可用工具 schema 列表;为 None / 空时跳过 tools 小节
        file_limit:files 小节最多取几条记录
        tokens_per_file:每个文件内容字符上限
        skills_budget:skills 小节总字符上限(累计)
        tokens_per_skill:每个 skill body 字符上限
    """
    sections: list[str] = []

    # 小节 1:最近读过的文件
    if state is not None:
        files = state.snapshot_files(file_limit)
        if files:
            lines = ["## 最近读过的文件\n"]
            for record in files:
                ts = _format_timestamp(record.timestamp)
                snippet = record.content[: tokens_per_file * 3]  # 字符上限
                if len(record.content) > len(snippet):
                    snippet += "\n... [truncated]"
                lines.append(
                    f"### `{record.path}` (read at {ts})\n"
                    f"```\n{snippet}\n```\n"
                )
            sections.append("\n".join(lines))

    # 小节 2:激活的 skills
    if state is not None:
        skills = state.snapshot_skills()
        if skills:
            lines = ["## 已激活的 Skills\n"]
            used = 0
            for record in skills:
                snippet = record.body[: tokens_per_skill * 3]
                if len(record.body) > len(snippet):
                    snippet += "\n... [truncated]"
                block = f"### `{record.name}`\n```\n{snippet}\n```\n"
                if used + len(block) > skills_budget * 3:
                    break
                lines.append(block)
                used += len(block)
            if len(lines) > 1:  # 真的有内容才加
                sections.append("\n".join(lines))

    # 小节 3:可用工具
    if tool_schemas:
        lines = ["## 可用工具\n"]
        for schema in tool_schemas:
            name = schema.get("name", "?")
            desc = (schema.get("description", "") or "").split("\n")[0].strip()
            lines.append(f"- **{name}**: {desc}")
        sections.append("\n".join(lines))

    # 小节 4:边界提示(只要前面有任何内容,都加这段)
    if sections:
        sections.append(
            "## 提示\n"
            "以上是压缩后重建的上下文。若需要某文件的原始内容或更详细的代码段,"
            "请用对应的工具(例如 ReadFile)按需加载,不要凭摘要脑补。"
        )

    return "\n\n".join(sections)
