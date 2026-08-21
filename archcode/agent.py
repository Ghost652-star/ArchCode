from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from pydantic import ValidationError

import asyncio

from archcode.conversation.manager import ConversationManager
from archcode.conversation.models import ThinkingBlock, ToolResultBlock, ToolUseBlock
from archcode.llm.client import LLMClient, LLMError, is_prompt_too_long_error
from archcode.llm.events import (
    StreamEnd,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)
from archcode.llm.serializer import build_anthropic_tools, build_openai_tools
from archcode.permissions import Decision, PermissionChecker, PermissionMode
from archcode.prompts import build_plan_mode_reminder
from archcode.tools.base import MAX_OUTPUT_CHARS, ToolResult
from archcode.tools.registry import ToolRegistry

# 压缩模块:可选依赖,没启用压缩时所有 hook 都跳过
from archcode.config import CompressionConfig
from archcode.context.compactor import (
    CompactCircuitBreaker,
    CompactEvent,
    ForceCompactBreaker,
    auto_compact,
    force_compact,
    should_auto_compact,
)
from archcode.context.manager import (
    ContentReplacementState,
    SINGLE_RESULT_CHAR_LIMIT,
    apply_tool_result_budget,
    ensure_session_dir,
    make_persisted_preview,
    persist_tool_result,
)
from archcode.context.recovery import RecoveryState


# ---------------------------------------------------------------------------
# AgentEvent 事件类型
# ---------------------------------------------------------------------------


@dataclass
class StreamText:
    text: str


@dataclass
class ThinkingText:
    text: str


@dataclass
class ToolUseEvent:
    tool_name: str
    tool_id: str
    arguments: dict


@dataclass
class ToolResultEvent:
    """工具执行结果事件（发往 UI 显示）。"""

    tool_id: str
    tool_name: str
    output: str
    is_error: bool
    elapsed: float


@dataclass
class PermissionRequest:
    """权限请求事件——HITL 层：agent 暂停等待用户确认。

    app.py 捕获此事件，弹出 PermissionModal；
    用户选择 allow/deny 后，通过 future.set_result(True/False) 解除阻塞。

    两种模式：
    - 权限询问（question=None, options=None）：弹 Yes/No
    - AskUserQuestion（question=..., options=[...]）：弹 LLM 给的选项
    """

    tool_name: str
    category: str
    reason: str
    future: asyncio.Future
    question: str | None = None
    options: list | None = None
    multi_select: bool = False


@dataclass
class TurnComplete:
    turn: int


@dataclass
class ErrorEvent:
    message: str


@dataclass
class LoopComplete:
    total_turns: int
    text: str = ""


@dataclass
class UsageEvent:
    input_tokens: int
    output_tokens: int
    cache_read: int = 0
    cache_creation: int = 0


@dataclass
class RetryEvent:
    reason: str
    wait: float = 0.0


@dataclass
class CompactStarted:
    """压缩开始事件 —— UI 用此挂载进度 widget。
    mode: "auto" / "manual" / "force" —— 区分触发来源。"""
    mode: str  # "auto" / "manual" / "force"


@dataclass
class CompactProgress:
    """压缩过程中,每收到一段流式摘要文本触发一次。
    UI 用此更新进度(字符数 / 摘要预览),不阻塞 agent loop。
    回调异常不能影响压缩主流程(已在 compactor.py 内部 try)。"""
    delta: str
    total_chars: int


@dataclass
class CompactFinished:
    """压缩结束事件 —— UI 用此卸载进度 widget,显示最终结果。
    success: 是否成功生成摘要并替换 history。
    dropped: 丢弃的消息数(success 时才有意义)。
    summary_preview: 摘要前 200 字符(success 时才有意义)。
    error: 失败原因(success=False 才有意义)。"""
    success: bool
    dropped: int = 0
    summary_preview: str = ""
    error: str = ""


AgentEvent = (
    StreamText
    | ThinkingText
    | ToolUseEvent
    | ToolResultEvent
    | PermissionRequest
    | TurnComplete
    | ErrorEvent
    | LoopComplete
    | UsageEvent
    | RetryEvent
    | CompactStarted
    | CompactProgress
    | CompactFinished
)


# ---------------------------------------------------------------------------
# LLM 响应收集器
# ---------------------------------------------------------------------------


@dataclass
class ThinkingBlock_:
    thinking: str
    signature: str


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCallComplete] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock_] = field(default_factory=list)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0


class StreamCollector:
    def __init__(self) -> None:
        self.response = LLMResponse()

    async def consume(
        self, stream: AsyncIterator[StreamEvent]
    ) -> AsyncIterator[AgentEvent]:
        """消费 LLM 流式事件,边折叠状态边向外 yield AgentEvent。

        调用方拿到的是 ``AgentEvent`` 流:
        - 折叠过的状态读 ``self.response``(text / tool_calls / thinking_blocks / stop_reason / token 用量)
        - 即时 UI 事件从 ``async for event in collector.consume(...)`` 直接拿到
        """
        async for event in stream:
            if isinstance(event, TextDelta):
                self.response.text += event.text
                yield StreamText(text=event.text)
            elif isinstance(event, ThinkingDelta):
                yield ThinkingText(text=event.text)
            elif isinstance(event, ThinkingComplete):
                self.response.thinking_blocks.append(
                    ThinkingBlock_(thinking=event.thinking, signature=event.signature)
                )
            elif isinstance(event, (ToolCallStart, ToolCallDelta)):
                pass
            elif isinstance(event, ToolCallComplete):
                self.response.tool_calls.append(event)
                yield ToolUseEvent(
                    tool_name=event.tool_name,
                    tool_id=event.tool_id,
                    arguments=event.arguments,
                )
            elif isinstance(event, StreamEnd):
                self.response.stop_reason = event.stop_reason
                self.response.input_tokens = event.input_tokens
                self.response.output_tokens = event.output_tokens
                self.response.cache_read = event.cache_read
                self.response.cache_creation = event.cache_creation


# ---------------------------------------------------------------------------
# 工具批量执行
# ---------------------------------------------------------------------------


@dataclass
class ToolBatch:
    concurrent: bool
    calls: list[ToolCallComplete]


def partition_tool_calls(
    tool_calls: list[ToolCallComplete],
    registry: ToolRegistry,
) -> list[ToolBatch]:
    """将 tool_calls 按并发安全性和 registry 状态分组。

    同一个 batch 内的 calls 可以并发执行；不同 batch 之间必须串行。
    """
    batches: list[ToolBatch] = []
    for tc in tool_calls:
        tool = registry.get(tc.tool_name)
        safe = (
            tool is not None
            and tool.is_concurrency_safe
            and registry.is_enabled(tc.tool_name)
        )
        if safe and batches and batches[-1].concurrent:
            batches[-1].calls.append(tc)
        else:
            batches.append(ToolBatch(concurrent=safe, calls=[tc]))
    return batches


# ---------------------------------------------------------------------------
# Agent 主循环
# ---------------------------------------------------------------------------


class Agent:
    """Agent 循环：用户消息 → LLM 流式事件 → 工具执行 → 结果写回 → 循环直到模型结束。

    完整 ReAct 循环（v0.3+）：
    - while True 驱动，max_iterations 为硬上限
    - 每轮：stream LLM → 收集 TextDelta + ToolCallComplete →
           无 tool_calls → TurnComplete + LoopComplete 退出
           有 tool_calls → 执行所有工具 → 写 ToolResultBlock →
           回到循环顶部继续
    - consecutive_unknown 连续 3 次未知工具则退出
    - max_tokens 停止原因触发重试逻辑

    Plan Mode（可选）：
    - 通过 ``plan_mode=True`` 开启,_execute_tool 拦截非 read 工具并提示用户先关掉开关
    - plan 文件路径:``<work_dir>/.archcode/plans/{slug}.md``
    - 用 ``set_plan_mode(True/False)`` 切换
    - plan reminder 不入 system 字段,通过每轮 ``conversation.add_system_reminder()`` 注入到 messages
    """

    # plan mode reminder 文本已搬到 prompts/reminders.py,这里只留路径生成

    _ADJECTIVES = ["bold", "bright", "calm", "deep", "fair", "fast",
                   "glad", "keen", "kind", "neat", "pure", "safe",
                   "soft", "warm", "wise", "swift", "vivid"]
    _NOUNS = ["sketch", "draft", "spark", "trail", "ridge", "grove",
              "field", "forge", "frost", "haven", "pearl", "stone",
              "river", "tower", "delta", "orbit", "pulse", "shore"]

    def __init__(
        self,
        client: LLMClient,
        system_prompt: str,
        tool_registry: ToolRegistry | None = None,
        permission_checker: PermissionChecker | None = None,
        max_output_tokens: int = 4096,
        max_iterations: int = 50,
        work_dir: str | Path | None = None,
        plan_mode: bool = False,
        compression: CompressionConfig | None = None,
    ) -> None:
        self._client = client
        self._system_prompt = system_prompt
        self._plan_mode = False
        self._plan_path: Path | None = None
        self._tool_registry = tool_registry
        self._permission_checker = permission_checker
        self._max_iterations = max_iterations
        self._work_dir = Path(work_dir).resolve() if work_dir else None
        self._client.set_max_output_tokens(max_output_tokens)
        if plan_mode:
            self.set_plan_mode(True)

        # ── 压缩子系统(可选) ───────────────────────────────
        # 当 compression is None 或 enabled=False 时,所有 hook 都不执行,
        # Agent 行为退化为「原样发请求」。
        self._compression: CompressionConfig | None = compression
        self._replacement_state: ContentReplacementState = ContentReplacementState()
        self._recovery_state: RecoveryState = RecoveryState()
        if compression is not None and compression.enabled and work_dir is not None:
            self._session_dir: Path | None = ensure_session_dir(work_dir)
            self._auto_compact_breaker = CompactCircuitBreaker(
                max_failures=compression.max_summary_failures
            )
            self._force_compact_breaker = ForceCompactBreaker(
                max_failures=compression.max_force_compact_failures
            )
        else:
            self._session_dir = None
            self._auto_compact_breaker = CompactCircuitBreaker()
            self._force_compact_breaker = ForceCompactBreaker()

        # 中断信号:app.py 按 Esc 时 set_reactive / set_event,
        # 默认 new 一个 Event 保证 FakeAgent 也能跑
        if not hasattr(self, "_abort_event"):
            self._abort_event: asyncio.Event = asyncio.Event()

    def set_plan_mode(self, on: bool) -> None:
        """切换 plan mode。

        plan reminder 不入 system 字段,而是通过每轮
        ``conversation.add_system_reminder()`` 注入到 messages 数组,
        开启时顺便生成 plan 文件路径。
        同步更新 permission_checker 的状态。
        """
        self._plan_mode = on
        if on:
            self._plan_path = self._get_plan_path()
            if self._permission_checker is not None:
                self._permission_checker.mode = PermissionMode.PLAN
                self._permission_checker.plan_file_path = (
                    str(self._plan_path) if self._plan_path else ""
                )
        else:
            if self._permission_checker is not None:
                self._permission_checker.mode = PermissionMode.DEFAULT
                self._permission_checker.plan_file_path = ""

    def _get_plan_path(self) -> Path:
        """生成 plan 文件路径 <work_dir>/.archcode/plans/{slug}.md。"""
        import datetime
        import random

        base = self._work_dir or Path.cwd()
        plans_dir = base / ".archcode" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%m%d-%H%M")
        slug = f"{random.choice(self._ADJECTIVES)}-{random.choice(self._NOUNS)}-{ts}"
        return plans_dir / f"{slug}.md"

    def _tool_schemas(self) -> list[dict[str, Any]] | None:
        """根据 client protocol 返回对应格式的工具 schema 列表。"""
        if self._tool_registry is None:
            return None
        protocol = self._client.protocol
        if protocol in ("openai", "openai-compat"):
            schemas = build_openai_tools(self._tool_registry.list_tools())
        else:
            schemas = build_anthropic_tools(self._tool_registry.list_tools())
        return schemas or None

    async def _execute_tool(
        self, tc: ToolCallComplete
    ) -> AsyncIterator[tuple[ToolResult | PermissionRequest, float, bool]]:
        """执行单个工具调用——async generator（照搬 MewCode）。

        yield 三种之一：
        1. PermissionRequest — HITL 暂停等用户
        2. (ToolResult, elapsed, is_unknown) — 工具执行结果（最后一次 yield）
        """
        if self._tool_registry is None:
            yield (
                ToolResult(output="no tool registry configured", is_error=True),
                0.0,
                False,
            )
            return

        tool = self._tool_registry.get(tc.tool_name)
        start = time.monotonic()

        if tool is None:
            yield (
                ToolResult(output=f"Error: unknown tool '{tc.tool_name}'", is_error=True),
                time.monotonic() - start,
                True,  # is_unknown
            )
            return

        if not self._tool_registry.is_enabled(tc.tool_name):
            yield (
                ToolResult(
                    output=f"Error: tool '{tc.tool_name}' is disabled", is_error=True
                ),
                time.monotonic() - start,
                False,
            )
            return

        # ── 权限判定：5 层检查
        if self._permission_checker is not None:
            decision = self._permission_checker.check(
                tool_name=tc.tool_name,
                category=getattr(tool, "category", "read"),
                arguments=tc.arguments,
            )
            if decision.effect == "deny":
                yield (
                    ToolResult(
                        output=(
                            f"权限拦截: {decision.reason}\n"
                            f"工具 '{tc.tool_name}' 被拒绝执行。"
                        ),
                        is_error=True,
                    ),
                    time.monotonic() - start,
                    False,
                )
                return
            if decision.effect == "ask":
                # HITL：yield PermissionRequest 出去，等用户回填 future
                future: asyncio.Future = asyncio.Future()
                question = None
                options = None
                multi_select = False
                if tc.tool_name == "AskUserQuestion":
                    args = tc.arguments or {}
                    question = args.get("question")
                    options = args.get("options")
                    multi_select = bool(args.get("multi_select", False))
                req = PermissionRequest(
                    tool_name=tc.tool_name,
                    category=getattr(tool, "category", "read"),
                    reason=decision.reason,
                    future=future,
                    question=question,
                    options=options,
                    multi_select=multi_select,
                )
                yield req
                value = await future

                # 解析用户决策
                if tc.tool_name == "AskUserQuestion":
                    user_answer = value if value else "用户没选择"
                    yield (
                        ToolResult(
                            output=f"用户选择了: {user_answer}",
                            is_error=False,
                        ),
                        0.0,
                        False,
                    )
                    return
                else:
                    allowed = bool(value)
                    if not allowed:
                        yield (
                            ToolResult(
                                output=f"用户拒绝了工具 '{tc.tool_name}' 的执行请求。",
                                is_error=True,
                            ),
                            0.0,
                            False,
                        )
                        return
                    # 允许 → 继续执行工具（透传到下面）

        try:
            params = tool.params_model.model_validate(tc.arguments)
            result = await tool.execute(params)
        except ValidationError as e:
            result = ToolResult(output=f"Error: invalid arguments: {e}", is_error=True)
        except Exception as e:
            result = ToolResult(output=f"Tool execution error: {e}", is_error=True)

        # 截断/落盘:先落盘(>50K 全文存磁盘可恢复),再硬截断(>10K 有损)
        # 顺序关键 —— 落盘必须在截断之前,>50K 的全文才能完整存下来
        result = self._maybe_persist_or_truncate(tc.tool_id, result)

        # 记录 Read 类工具的输出,供压缩后恢复上下文用
        self._record_recovery_data(tool, tc, result)

        yield result, time.monotonic() - start, False

    def _record_recovery_data(
        self, tool: ToolResult | None, tc: ToolCallComplete, result: ToolResult
    ) -> None:
        """Record file reads + skill invocations for the recovery attachment.

        File reads are recorded only for ``category == "read"`` tools (matches the
        safety model: Bash / Write don't get recorded as context to recover).
        Skill invocations are a no-op until ``skills/`` ships its loader.

        Failures (network, validation) are NOT recorded — the recovery attachment
        only shows what the model successfully saw.
        """
        if result.is_error or self._tool_registry is None:
            return
        tool_def = self._tool_registry.get(tc.tool_name)
        if tool_def is None:
            return
        category = getattr(tool_def, "category", "read")
        args = tc.arguments or {}
        if category == "read" and isinstance(args.get("path"), str):
            self._recovery_state.record_file_read(args["path"], result.output)
        # skills/ 是空包,接口预留;后续接入时:
        # if category == "skill" and isinstance(args.get("name"), str):
        #     self._recovery_state.record_skill_invocation(args["name"], result.output)

    @staticmethod
    def _truncate_tool_result(result: ToolResult) -> ToolResult:
        """工具结果超 MAX_OUTPUT_CHARS 就在尾部加 [TRUNCATED] 标记。

        LLM 看到 [TRUNCATED] 关键字就知道内容被砍,自己会看 schema 找补救方法
        (比如 ReadFile 的 offset/limit)。不写太具体的提示,让模型自己判断。
        """
        if len(result.output) <= MAX_OUTPUT_CHARS:
            return result
        kept = result.output[:MAX_OUTPUT_CHARS]
        full_size = len(result.output)
        return ToolResult(
            output=(
                f"{kept}\n\n"
                f"[TRUNCATED: shown {MAX_OUTPUT_CHARS:,}, full {full_size:,} chars]"
            ),
            is_error=result.is_error,
        )

    def _maybe_persist_or_truncate(
        self, tool_use_id: str, result: ToolResult
    ) -> ToolResult:
        """工具结果先落盘、再硬截断 —— 照搬 MewCode 的顺序。

        - len > SINGLE_RESULT_CHAR_LIMIT (50K) → 全文写磁盘,inline 换 preview,
          可恢复。落盘检查在截断之前,所以 >50K 的全文能完整存下来。
        - 否则交给 _truncate_tool_result:10K-50K 硬截断(有损),≤10K 原样。

        落盘后同步登记进 ``_replacement_state``,Layer 1 的三个 Pass 就会跳过
        这条 id —— 避免把已经换成 preview 的结果再 snip 掉、丢掉文件指针。

        ``_session_dir`` 为 None(compression 关闭)时退化为只截断。
        """
        content = result.output
        if (
            len(content) > SINGLE_RESULT_CHAR_LIMIT
            and self._session_dir is not None
        ):
            fp = persist_tool_result(tool_use_id, content, self._session_dir)
            if fp is not None:
                # 登记:Layer 1 Pass 1/2/3 见到此 id 直接跳过,不再动 preview
                self._replacement_state.replacements[tool_use_id] = str(fp)
                self._replacement_state.seen_ids.add(tool_use_id)
                return ToolResult(
                    output=make_persisted_preview(content, tool_use_id, fp),
                    is_error=result.is_error,
                )
            # fp is None:文件已存在(并发/重放),用已知路径重生成稳定 preview
            known_path = self._session_dir / f"{tool_use_id}.txt"
            self._replacement_state.replacements[tool_use_id] = str(known_path)
            self._replacement_state.seen_ids.add(tool_use_id)
            return ToolResult(
                output=make_persisted_preview(content, tool_use_id, known_path),
                is_error=result.is_error,
            )
        return self._truncate_tool_result(result)

    async def _execute_batch_parallel(
        self, calls: list[ToolCallComplete]
    ) -> list[tuple[ToolCallComplete, ToolResult, float, bool]]:
        """并发执行同一个 batch 内的所有工具调用。

        HITL 不走并发路径——batch 里只要有 ask 的工具就会被拆成串行，
        不会走到这里。这里只处理纯安全工具的并发。
        """
        import asyncio

        async def run_one(tc: ToolCallComplete) -> tuple[ToolCallComplete, ToolResult, float, bool]:
            # 收集最后一次 yield（必定是 ToolResult，不会有 PermissionRequest）
            final = None
            async for item in self._execute_tool(tc):
                if isinstance(item, PermissionRequest):
                    # 并发路径不应触发 HITL（ask 的工具不并发），跳过即可
                    continue
                final = item
            if final is None:
                return tc, ToolResult(output="no result", is_error=True), 0.0, False
            result, elapsed, is_unknown = final
            return tc, result, elapsed, is_unknown

        tasks = [run_one(tc) for tc in calls]
        return list(await asyncio.gather(*tasks))

    async def run(
        self,
        user_input: str,
        conversation: ConversationManager,
    ) -> AsyncIterator[AgentEvent]:
        conversation.add_user(user_input)

        iteration = 0
        consecutive_unknown = 0
        final_text = ""
        # 中断信号:app.py 按 Esc → self._abort_event.set()
        # loop 每轮开头 + 每个 stream 事件点检查 → 立刻退出
        abort = self._abort_event

        while True:
            iteration += 1

            # 用户主动打断:每轮开头检查,避免半路强行 cancel SDK 请求
            if abort.is_set():
                yield ErrorEvent(message="[aborted] 用户取消")
                yield LoopComplete(total_turns=iteration, text=final_text)
                return

            # 硬上限
            if iteration > self._max_iterations:
                yield ErrorEvent(
                    message=f"Agent reached maximum iterations ({self._max_iterations})"
                )
                yield LoopComplete(total_turns=iteration, text=final_text)
                return

            # 每轮重新注入 plan mode reminder(对话历史可能会污染 LLM 判断)
            # ── 动态上下文注入点(扩展契约)────────────────────────────────
            # 当前只注入 plan reminder。将来 memory / skills / hooks /
            # CLAUDE.md 指令 等子系统落地时,就在这个 block 里 add 一个
            # conversation.add_system_reminder(<那段内容>)。
            # 注意:不要动 self._system_prompt(那会破 Anthropic prompt cache),
            # 任何会变的内容都走 conversation.add_system_reminder 这条路。
            # 详细设计见 docs/prompts-design.md。
            if self._plan_mode and self._plan_path is not None:
                work_dir_str = str(self._work_dir) if self._work_dir else None
                conversation.add_system_reminder(
                    build_plan_mode_reminder(
                        plan_path=str(self._plan_path),
                        work_dir=work_dir_str,
                    )
                )

            # ── MCP 延迟工具注入(独立于 plan_mode,每轮都注) ────
            if self._tool_registry is not None:
                deferred_names = self._tool_registry.get_deferred_tool_names()
                if deferred_names:
                    conversation.add_system_reminder(
                        "以下工具可通过 ToolSearch 加载(完整 schema 默认不发):\n"
                        + "\n".join(f"  - {n}" for n in deferred_names)
                        + '\n用法:ToolSearch(query="select:name1,name2") 或 '
                        + 'ToolSearch(query="关键词")'
                    )

            # ── 上下文压缩:Layer 1 (单条预算) + Layer 2 (累积阈值) ──
            # 顺序:先轻量(per-message budget),再昂贵(LLM 摘要)
            if (
                self._compression is not None
                and self._compression.enabled
                and self._session_dir is not None
            ):
                try:
                    apply_tool_result_budget(
                        conversation=conversation,
                        session_dir=self._session_dir,
                        state=self._replacement_state,
                        single_char_limit=self._compression.single_char_limit,
                        aggregate_char_limit=self._compression.aggregate_char_limit,
                        preview_chars=self._compression.preview_chars,
                        old_result_snip_chars=self._compression.old_result_snip_chars,
                        keep_recent_turns=self._compression.keep_recent_turns,
                    )
                except Exception:
                    # Layer 1 失败不能阻塞 agent loop,降级到原样发
                    pass

                if should_auto_compact(
                    conversation.current_tokens(), self._client.context_window
                ):
                    # Layer 2: 摘要
                    # 用 on_started 回调:真正开始调 LLM 才标记 started,
                    # auto_compact 跑完后才 yield CompactStarted,避免
                    # 「阈值过但 to_summarize 空」时短暂挂 widget
                    progress_chars = [0]
                    started_flag: list[str] = []  # 长度=1 表示 started 已触发

                    def _on_progress(delta: str) -> None:
                        progress_chars[0] += len(delta)

                    def _on_started() -> None:
                        if not started_flag:
                            started_flag.append("started")

                    try:
                        tool_schemas = self._tool_schemas()
                        event = await auto_compact(
                            conversation=conversation,
                            client=self._client,
                            context_window=self._client.context_window,
                            session_dir=self._session_dir,
                            recovery=self._recovery_state,
                            tool_schemas=tool_schemas,
                            breaker=self._auto_compact_breaker,
                            manual=False,
                            keep_recent_tokens=self._compression.keep_recent_tokens,
                            keep_max_tokens=self._compression.keep_max_tokens,
                            min_keep_messages=self._compression.min_keep_messages,
                            min_summarize_prefix_tokens=self._compression.min_summarize_prefix_tokens,
                            max_retries=self._compression.max_summary_retries,
                            on_text_delta=_on_progress,
                            on_started=_on_started,
                        )
                        # 只在 started_flag 非空时才发任何进度事件
                        if started_flag:
                            yield CompactStarted(mode="auto")
                            if progress_chars[0] > 0:
                                yield CompactProgress(
                                    delta="",
                                    total_chars=progress_chars[0],
                                )
                        if isinstance(event, str):
                            # 失败 / 熔断 → 注入 system_reminder 让模型看到
                            conversation.add_system_reminder(
                                f"[compression] {event}"
                            )
                            if started_flag:
                                yield CompactFinished(
                                    success=False, error=event
                                )
                        elif event is None:
                            # 阈值过但 to_summarize 空 — Widget 没挂,啥也不发
                            pass
                        elif isinstance(event, CompactEvent):
                            snippet = event.summary[:200].replace("\n", " ")
                            yield CompactFinished(
                                success=True,
                                dropped=event.dropped_messages,
                                summary_preview=snippet,
                            )
                    except Exception as e:
                        # 摘要异常不能阻塞 agent loop
                        msg = f"自动压缩异常: {type(e).__name__}: {e}"
                        conversation.add_system_reminder(f"[compression] {msg}")
                        if started_flag:
                            yield CompactStarted(mode="auto")
                            yield CompactFinished(success=False, error=msg)

            # 构造 LLM 响应收集器
            # ``result_collector`` 在 force-compact 重试成功后会被替换为新收集器
            # 这样下游 (record_usage_anchor / add_assistant_message) 读的就是
            # 重试那次的 response。
            result_collector = StreamCollector()

            try:
                stream_iter = result_collector.consume(
                    self._client.stream(
                        conversation,
                        system=self._system_prompt,
                        tools=self._tool_schemas(),
                    )
                )
                while True:
                    # 每 yield 一次前检查 abort — 取消的话立刻 break,
                    # 不会把 StreamEnd / partial text 加进 conversation
                    if abort.is_set():
                        break
                    try:
                        event = await anext(stream_iter)
                    except StopAsyncIteration:
                        break
                    yield event
                    if abort.is_set():
                        break

            except LLMError as e:
                # prompt 超出窗口 → 触发 force-compact 重试一次
                if (
                    self._compression is not None
                    and self._compression.enabled
                    and self._session_dir is not None
                    and is_prompt_too_long_error(e)
                ):
                    # 仅在 force_compact 真要调 LLM 时,才向 UI 发 Started
                    # (跟 auto_compact 保持一致:熔断 / 空 history / 空 to_summarize 都跳过)
                    force_progress_chars = [0]
                    force_started_flag: list[str] = []

                    def _on_force_progress(delta: str) -> None:
                        force_progress_chars[0] += len(delta)

                    def _on_force_started() -> None:
                        if not force_started_flag:
                            force_started_flag.append("started")

                    try:
                        tool_schemas = self._tool_schemas()
                        compact_event = await force_compact(
                            conversation=conversation,
                            client=self._client,
                            context_window=self._client.context_window,
                            session_dir=self._session_dir,
                            recovery=self._recovery_state,
                            tool_schemas=tool_schemas,
                            breaker=self._force_compact_breaker,
                            keep_recent_tokens=self._compression.keep_recent_tokens,
                            keep_max_tokens=self._compression.keep_max_tokens,
                            min_keep_messages=self._compression.min_keep_messages,
                            min_summarize_prefix_tokens=self._compression.min_summarize_prefix_tokens,
                            max_retries=self._compression.max_summary_retries,
                            on_text_delta=_on_force_progress,
                            on_started=_on_force_started,
                        )
                        # 仅在真的进 LLM 之后,才把 Started/Progress 事件投出去
                        if force_started_flag:
                            yield CompactStarted(mode="force")
                            if force_progress_chars[0] > 0:
                                yield CompactProgress(
                                    delta="",
                                    total_chars=force_progress_chars[0],
                                )
                        if isinstance(compact_event, CompactEvent):
                            snippet = compact_event.summary[:200].replace("\n", " ")
                            if force_started_flag:
                                yield CompactFinished(
                                    success=True,
                                    dropped=compact_event.dropped_messages,
                                    summary_preview=snippet,
                                )
                            # 重试一次
                            retry_collector = StreamCollector()
                            try:
                                async for event in retry_collector.consume(
                                    self._client.stream(
                                        conversation,
                                        system=self._system_prompt,
                                        tools=self._tool_schemas(),
                                    )
                                ):
                                    yield event
                                result_collector = retry_collector
                            except LLMError:
                                # 重试仍失败 → 走错误路径
                                yield ErrorEvent(message=str(e))
                                yield LoopComplete(
                                    total_turns=iteration, text=final_text
                                )
                                return
                        else:
                            # force_compact 也挂了(熔断 / 空 history / 空 to_summarize / 摘要失败)
                            err_msg = (
                                compact_event
                                if isinstance(compact_event, str)
                                else "未知失败"
                            )
                            if force_started_flag:
                                yield CompactFinished(success=False, error=err_msg)
                            yield ErrorEvent(
                                message=f"[force-compact 失败] {err_msg}"
                            )
                            yield LoopComplete(
                                total_turns=iteration, text=final_text
                            )
                            return
                    except Exception as fc_err:
                        fc_msg = f"[force-compact 异常] {type(fc_err).__name__}: {fc_err}"
                        if force_started_flag:
                            yield CompactStarted(mode="force")
                            yield CompactFinished(success=False, error=fc_msg)
                        yield ErrorEvent(message=fc_msg)
                        yield LoopComplete(total_turns=iteration, text=final_text)
                        return
                else:
                    yield ErrorEvent(message=str(e))
                    yield LoopComplete(total_turns=iteration, text=final_text)
                    return
            except Exception as e:
                yield ErrorEvent(message=str(e))
                yield LoopComplete(total_turns=iteration, text=final_text)
                return

            # 从收集器取 tool_calls
            tool_calls = result_collector.response.tool_calls

            # 记录 token 用量
            conversation.record_usage_anchor(
                result_collector.response.input_tokens,
                result_collector.response.output_tokens,
                result_collector.response.cache_read,
                result_collector.response.cache_creation,
            )
            yield UsageEvent(
                input_tokens=result_collector.response.input_tokens,
                output_tokens=result_collector.response.output_tokens,
                cache_read=result_collector.response.cache_read,
                cache_creation=result_collector.response.cache_creation,
            )

            # 处理 max_tokens 停止原因:将当前输出接续到下一轮
            if result_collector.response.stop_reason == "max_tokens":
                # 简单重试：将当前输出接续到下一轮
                if result_collector.response.text:
                    conversation.add_assistant_message(result_collector.response.text)
                    conversation.add_user_message(
                        "Output token limit hit. Resume directly where you stopped. "
                        "Do not apologize or repeat previous content."
                    )
                yield RetryEvent(reason="max_tokens continuation")
                continue

            final_text = result_collector.response.text

            # 无 tool_calls → 本轮结束，退出循环
            if not tool_calls:
                conv_thinking = [
                    ThinkingBlock(thinking=tb.thinking, signature=tb.signature)
                    for tb in result_collector.response.thinking_blocks
                ]
                conversation.add_assistant_message(
                    result_collector.response.text,
                    thinking_blocks=conv_thinking or None,
                )
                yield TurnComplete(turn=iteration)
                yield LoopComplete(total_turns=iteration, text=final_text)
                return

            # 有 tool_calls → 记录 assistant 回复（含 tool_uses）
            uses = [
                ToolUseBlock(
                    tool_use_id=tc.tool_id,
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                )
                for tc in tool_calls
            ]
            conv_thinking = [
                ThinkingBlock(thinking=tb.thinking, signature=tb.signature)
                for tb in result_collector.response.thinking_blocks
            ]
            conversation.add_assistant_message(
                result_collector.response.text,
                tool_uses=uses,
                thinking_blocks=conv_thinking or None,
            )

            # 执行工具分组：同一 batch 可并发，不同 batch 串行
            tool_results: list[ToolResultBlock] = []
            batches = partition_tool_calls(tool_calls, self._tool_registry)

            for batch in batches:
                if batch.concurrent and len(batch.calls) > 1:
                    # 并发执行
                    batch_results = await self._execute_batch_parallel(batch.calls)
                    for tc, result, elapsed, is_unknown in batch_results:
                        if is_unknown:
                            consecutive_unknown += 1
                        else:
                            consecutive_unknown = 0

                        block = ToolResultBlock(
                            tool_use_id=tc.tool_id,
                            content=result.output,
                            is_error=result.is_error,
                        )
                        tool_results.append(block)
                        yield ToolResultEvent(
                            tool_id=tc.tool_id,
                            tool_name=tc.tool_name,
                            output=result.output,
                            is_error=result.is_error,
                            elapsed=elapsed,
                        )
                else:
                    # 串行执行：async for 处理 _execute_tool 的 yield
                    for tc in batch.calls:
                        result = None
                        elapsed = 0.0
                        is_unknown = False
                        async for item in self._execute_tool(tc):
                            if isinstance(item, PermissionRequest):
                                # HITL: yield 给 app.py 处理（app 端 set_result 后解除）
                                yield item
                                continue
                            result, elapsed, is_unknown = item

                        if result is None:
                            # 工具被取消 / 没结果
                            continue

                        if is_unknown:
                            consecutive_unknown += 1
                        else:
                            consecutive_unknown = 0

                        block = ToolResultBlock(
                            tool_use_id=tc.tool_id,
                            content=result.output,
                            is_error=result.is_error,
                        )
                        tool_results.append(block)
                        yield ToolResultEvent(
                            tool_id=tc.tool_id,
                            tool_name=tc.tool_name,
                            output=result.output,
                            is_error=result.is_error,
                            elapsed=elapsed,
                        )

            # 连续未知工具超过 3 次 → 退出
            if consecutive_unknown >= 3:
                yield ErrorEvent(
                    message="Agent terminated: too many consecutive unknown tool calls"
                )
                yield LoopComplete(total_turns=iteration, text=final_text)
                return

            # 将 tool results 写回对话，进入下一轮
            conversation.add_tool_results_message(tool_results)
            yield TurnComplete(turn=iteration)

    async def run_to_completion(
        self,
        user_input: str,
        conversation: ConversationManager,
    ) -> str:
        result = ""
        async for event in self.run(user_input, conversation):
            if isinstance(event, LoopComplete):
                result = event.text
            elif isinstance(event, ErrorEvent):
                raise RuntimeError(event.message)
        return result
