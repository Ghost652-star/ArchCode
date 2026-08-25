"""Layer 1:工具结果预算(per-message budget enforcement)。

每轮 agent loop 之前,检查 ``ConversationManager.history`` 里所有
``tool_result`` 块的大小。三 Pass:

1. **单条超限** — ``len(content) > SINGLE_RESULT_CHAR_LIMIT`` 时落盘 + preview
2. **单条消息内聚合** — ``sum(本条消息所有 tool_result) > AGGREGATE_CHAR_LIMIT``
   时按长度倒序挑大的逐个替换,直到总和不超限
3. **陈旧裁剪** — 最近 ``KEEP_RECENT_TURNS`` 个已完成用户轮次之前的 tool_result,
   只保留前 ``OLD_RESULT_SNIP_CHARS`` 字符 + ``<snipped>`` 标签

**决策冻结**:每条 tool_result 一旦被评估,``tool_use_id`` 进 ``seen_ids``,
决定进 ``replacements``。后续轮次该 id 命中直接套用历史决策,**不重新跑阈值**
—— 这是 prompt cache 前缀字节级稳定的核心机制。

落盘文件命名 ``<tool_use_id>.txt``,用 ``O_WRONLY|O_CREAT|O_EXCL`` 幂等写,
同一会话内多次触发只第一次真正写,字节稳定。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from archcode.conversation.manager import ConversationManager
from archcode.conversation.models import Message, ToolResultBlock


# ── 阈值常量(默认来自 CompressionConfig,但允许 manager.py 调用方直接传) ──

SINGLE_RESULT_CHAR_LIMIT = 50_000
AGGREGATE_CHAR_LIMIT = 200_000
PREVIEW_CHARS = 2_000
OLD_RESULT_SNIP_CHARS = 200
KEEP_RECENT_TURNS = 10
TOOL_RESULTS_DIR = "tool-results"


@dataclass
class ContentReplacementRecord:
    """单条替换的记录(目前仅用于测试断言 + 未来持久化)。

    字段:
    - tool_use_id:被替换的 tool_result id
    - reason:"single" / "aggregate" / "stale"
    - preview_len:替换后 preview 字节数
    - persisted_path:落盘文件路径(单条 / 聚合场景);stale 场景为 None
    """

    tool_use_id: str
    reason: str  # "single" | "aggregate" | "stale"
    preview_len: int
    persisted_path: Path | None = None


@dataclass
class ContentReplacementState:
    """跨轮决策冻结状态。

    - ``seen_ids``:已经被评估过的 ``tool_use_id``
    - ``replacements``:被替换的 ``tool_use_id`` → 落盘路径(用于 build_recovery)
    - ``per_message_evaluated``:已经跑过 Pass 2 的 message 列表
      (避免下一轮重复扫描;Pass 3 走 turn 计数自然处理)

    注意:这是进程内状态,不做跨会话持久化。ArchCode 没有 session resume。
    """

    seen_ids: set[str] = field(default_factory=set)
    replacements: dict[str, str] = field(default_factory=dict)
    per_message_evaluated: set[int] = field(default_factory=set)


# ── 落盘语义 ────────────────────────────────────────────────────────


def ensure_session_dir(work_dir: Path | str) -> Path:
    """建立 ``<work_dir>/.archcode/session/tool-results/`` 目录。

    落盘文件都进这里。ArchCode 启动时会调一次,后续 ``apply_tool_result_budget``
    用的就是这个目录。
    """
    session_dir = Path(work_dir) / ".archcode" / "session" / TOOL_RESULTS_DIR
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def persist_tool_result(
    tool_use_id: str, content: str, session_dir: Path
) -> Path | None:
    """wx 模式落盘 + 幂等。

    - 第一次:写文件,返回路径
    - 后续(同一 tool_use_id):FileExistsError 被吞,返回 None

    **关键约束**:文件字节必须完全稳定 —— 同一会话内多次序列化时,
    内容相同的 tool_result 必须得到相同的 preview 字符串,否则 prompt cache
    前缀会断。
    """
    file_path = session_dir / f"{tool_use_id}.txt"
    try:
        fd = os.open(str(file_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        # 写入失败就清掉半成品,别留垃圾
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return file_path


def make_persisted_preview(
    content: str,
    tool_use_id: str,
    persisted_path: Path,
    preview_chars: int = PREVIEW_CHARS,
) -> str:
    """生成落盘后的 preview 字符串。

    格式::

        <persisted-output>
        tool_use_id: toolu_xxx
        file: /abs/path/to/toolu_xxx.txt
        preview (first {N} chars of {total}):

        <前 N 字符>
        ... [truncated, full content at <file>]
        </persisted-output>

    同样的 content + tool_use_id 必须得到相同的字符串(append 进 history 后,
    prompt cache 前缀字节稳定)。
    """
    total_chars = len(content)
    snippet = content[:preview_chars]
    truncated = total_chars > preview_chars

    parts = [
        "<persisted-output>",
        f"tool_use_id: {tool_use_id}",
        f"file: {persisted_path}",
        f"preview (first {preview_chars} chars of {total_chars}):",
        "",
        snippet,
    ]
    if truncated:
        parts.append(f"... [truncated, full content at {persisted_path}]")
    parts.append("</persisted-output>")
    return "\n".join(parts)


# ── 三 Pass 主函数 ────────────────────────────────────────────────


def _old_completed_turn_region_end(
    history: list[Message], keep_recent_turns: int
) -> int:
    """返回可被 Pass 3 处理的旧历史右边界（exclusive）。

    从最新消息向前数已完成的用户级轮次；最近 ``keep_recent_turns`` 个
    完成轮次及其后的活跃尾部不进入旧区域。返回的边界包含第一个需要裁剪的
    旧轮次的最终 assistant 消息，因此该轮次前面的 tool_result 会一并处理。
    """
    completed_seen = 0
    for index in range(len(history) - 1, -1, -1):
        if not history[index].completes_user_turn:
            continue

        completed_seen += 1
        if completed_seen > keep_recent_turns:
            return index + 1

    return 0


def _align_message_with_tool_pair(
    history: list[Message], start_index: int
) -> int:
    """如果 ``start_index`` 落在带 ``tool_results`` 的 user 消息上,
    把它往前挪到对应 assistant ``tool_use`` 消息,保证配对完整。

    返回调整后的 start_index(一定 ≤ 原值)。如果找不到配对的 assistant,
    不动(返回原值)。
    """
    if start_index >= len(history):
        return start_index
    msg = history[start_index]
    if not msg.tool_results:
        return start_index

    # 拿这条消息里所有 tool_use_id,往前找对应 assistant.tool_uses
    needed_ids = {tr.tool_use_id for tr in msg.tool_results}
    for j in range(start_index - 1, -1, -1):
        cand = history[j]
        if cand.role == "assistant" and cand.tool_uses:
            cand_ids = {tu.tool_use_id for tu in cand.tool_uses}
            if needed_ids & cand_ids:
                return j
    # 找不到配对 — 说明这条消息本身就是 orphan,不动
    return start_index


def apply_tool_result_budget(
    conversation: ConversationManager,
    session_dir: Path,
    state: ContentReplacementState,
    *,
    single_char_limit: int = SINGLE_RESULT_CHAR_LIMIT,
    aggregate_char_limit: int = AGGREGATE_CHAR_LIMIT,
    preview_chars: int = PREVIEW_CHARS,
    old_result_snip_chars: int = OLD_RESULT_SNIP_CHARS,
    keep_recent_turns: int = KEEP_RECENT_TURNS,
) -> list[ContentReplacementRecord]:
    """原地修改 ``conversation.history``,返回本轮新增的替换记录。

    三个 Pass 顺序执行,Pass 1 + 2 互斥(同一 tool_use_id 不会既被 Pass 1
    又被 Pass 2 处理),Pass 3 是独立维度(陈旧裁剪)。
    """
    records: list[ContentReplacementRecord] = []
    history = conversation.history

    # ── Pass 1:单条超限 ──────────────────────────────────────────
    for message in history:
        if not message.tool_results:
            continue
        new_results: list[ToolResultBlock] = []
        mutated = False
        for tr in message.tool_results:
            if tr.tool_use_id in state.seen_ids:
                # 已评估过:如果决定过替换,用冻结的 preview
                if tr.tool_use_id in state.replacements:
                    # 重新构造 preview (因为是 in-memory,丢过原内容)
                    # 实际上 record 已经被替换成 preview 了,保持不变
                    pass
                new_results.append(tr)
                continue

            state.seen_ids.add(tr.tool_use_id)
            if len(tr.content) <= single_char_limit:
                new_results.append(tr)
                continue

            # 落盘 + preview
            path = persist_tool_result(tr.tool_use_id, tr.content, session_dir)
            if path is None:
                # 已存在(并发场景),不重写,保持原 content 不变
                new_results.append(tr)
                continue
            preview = make_persisted_preview(
                tr.content, tr.tool_use_id, path, preview_chars
            )
            state.replacements[tr.tool_use_id] = str(path)
            records.append(
                ContentReplacementRecord(
                    tool_use_id=tr.tool_use_id,
                    reason="single",
                    preview_len=len(preview),
                    persisted_path=path,
                )
            )
            new_results.append(
                ToolResultBlock(
                    tool_use_id=tr.tool_use_id,
                    content=preview,
                    is_error=tr.is_error,
                )
            )
            mutated = True
        if mutated:
            message.tool_results = new_results

    # ── Pass 2:单消息聚合超限 ────────────────────────────────────
    for idx, message in enumerate(history):
        if not message.tool_results:
            continue
        # 用 message id 作为评估单元 key (list id)
        # 用 idx 不稳定(insert 会变),改成对 (idx, len(message.tool_results))
        eval_key = (idx, len(message.tool_results))
        if eval_key in state.per_message_evaluated:
            continue
        state.per_message_evaluated.add(eval_key)

        total = sum(len(tr.content) for tr in message.tool_results)
        if total <= aggregate_char_limit:
            continue

        # 按长度倒序逐个替换,直到总和 ≤ 阈值
        results = list(message.tool_results)
        order = sorted(
            range(len(results)), key=lambda i: len(results[i].content), reverse=True
        )
        running_total = total
        for i in order:
            tr = results[i]
            if running_total <= aggregate_char_limit:
                break
            # 已经决策过的(可能本轮 Pass 1 替换过)跳过
            if tr.tool_use_id in state.replacements:
                # 已经是 preview 长度,不会膨胀,可以不动
                continue
            path = persist_tool_result(tr.tool_use_id, tr.content, session_dir)
            if path is None:
                continue  # 并发已存在
            preview = make_persisted_preview(
                tr.content, tr.tool_use_id, path, preview_chars
            )
            state.replacements[tr.tool_use_id] = str(path)
            records.append(
                ContentReplacementRecord(
                    tool_use_id=tr.tool_use_id,
                    reason="aggregate",
                    preview_len=len(preview),
                    persisted_path=path,
                )
            )
            new_tr = ToolResultBlock(
                tool_use_id=tr.tool_use_id,
                content=preview,
                is_error=tr.is_error,
            )
            results[i] = new_tr
            running_total += len(preview) - len(tr.content)

        message.tool_results = results

    # ── Pass 3:陈旧裁剪 ─────────────────────────────────────────
    # 只裁最近 N 个已完成用户轮次之前的工具结果；未完成的活跃尾部保留原样。
    old_region_end = _old_completed_turn_region_end(history, keep_recent_turns)
    for message in history[:old_region_end]:
        if not message.tool_results:
            continue

        mutated = False
        new_results: list[ToolResultBlock] = []
        for tr in message.tool_results:
            # 已经替换过的(preview 形式),不动
            if tr.tool_use_id in state.replacements:
                new_results.append(tr)
                continue
            # 已经 snipped 过的(本轮可能反复跑),不动
            if tr.content.endswith("<snipped>"):
                new_results.append(tr)
                continue
            if len(tr.content) <= old_result_snip_chars:
                new_results.append(tr)
                continue

            snippet = tr.content[:old_result_snip_chars]
            snipped = f"{snippet}\n... <snipped>"
            new_results.append(
                ToolResultBlock(
                    tool_use_id=tr.tool_use_id,
                    content=snipped,
                    is_error=tr.is_error,
                )
            )
            records.append(
                ContentReplacementRecord(
                    tool_use_id=tr.tool_use_id,
                    reason="stale",
                    preview_len=len(snipped),
                    persisted_path=None,
                )
            )
            mutated = True
        if mutated:
            message.tool_results = new_results

    return records


def cleanup_tool_results(
    session_dir: Path,
    *,
    retained_tool_use_ids: set[str] | None = None,
) -> None:
    """压缩完成后删除不再被 history 引用的落盘工具结果。

    ``retained_tool_use_ids`` 为 ``None`` 时保持旧行为：清空整个目录。
    传入 keep_tail 中的 id 集合时，只保留对应的 ``<tool_use_id>.txt``，
    避免保留的原始工具结果 preview 指向已删除文件。

    必须在 ``conversation.replace_history(...)`` 之后调用 —— 失败路径
    不清理，避免新旧状态混搭。
    """
    if retained_tool_use_ids is None:
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
        session_dir.mkdir(parents=True, exist_ok=True)
        return

    session_dir.mkdir(parents=True, exist_ok=True)
    retained_files = {f"{tool_use_id}.txt" for tool_use_id in retained_tool_use_ids}
    for entry in session_dir.iterdir():
        if entry.is_file() and entry.name in retained_files:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
