"""HookEngine:加载校验、按事件分桶、observe/gate 双通道派发(hooks-design §6/§6.1)。

- ``from_config``:原始映射列表 → Hook 数据类 + 五条校验 + 按事件分桶;非法条目
  产定位诊断(来源层+序号+id)后丢弃,引擎照常就绪(单条坏配置不杀死应用)。
- ``observe``:非拦截事件,fire-and-forget(async hook 走 create_task),失败只记日志。
- ``gate``:pre_tool_use / permission_request 的裁决通道;reject 即短路返回哨兵。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from archcode.hooks.conditions import ConditionGroup
from archcode.hooks.executors import execute_action
from archcode.hooks.models import (
    GATE_EVENTS,
    VALID_EVENTS,
    VALID_EXECUTOR_TYPES,
    _TYPE_REQUIRED_FIELD,
    Action,
    Hook,
    HookContext,
    HookNotification,
    HookResult,
    ToolRejectedError,
)

log = logging.getLogger(__name__)


def _validate(raw: dict, source: str, index: int) -> tuple[Hook | None, str]:
    """单条 hook 的五条校验(§6 清单)。返回 (Hook|None, 诊断串)。诊断空串 = 通过。"""

    def _diag(msg: str) -> str:
        hid = raw.get("id") or "(no id)"
        return f"[hooks] {source}#{index} (id={hid}): {msg}"

    # ── 事件名 ──
    event = raw.get("event")
    if event not in VALID_EVENTS:
        return None, _diag(f"unknown event {event!r} (valid: {sorted(VALID_EVENTS)})")

    # ── id ──
    hook_id = str(raw.get("id") or f"{event}#{index}")

    # ── action ──
    action_raw = raw.get("action")
    if not isinstance(action_raw, dict):
        return None, _diag("missing 'action' block")
    action_type = action_raw.get("type")
    if action_type not in VALID_EXECUTOR_TYPES:
        return None, _diag(
            f"unknown action type {action_type!r} (valid: {sorted(VALID_EXECUTOR_TYPES)})"
        )

    # ── reject 作用域:仅 Gate 事件 ──
    reject = bool(raw.get("reject", False))
    if reject and event not in GATE_EVENTS:
        return None, _diag(
            f"'reject' only valid on gate events {sorted(GATE_EVENTS)}, not {event!r}"
        )

    # ── async 作用域:Gate 事件禁用;prompt 型注入也禁用(注入须赶在下一轮请求前)──
    async_exec = bool(raw.get("async", False))
    if async_exec and event in GATE_EVENTS:
        return None, _diag(f"'async' not allowed on gate event {event!r}")
    if async_exec and action_type == "prompt":
        return None, _diag("'async' not allowed for prompt-type hooks (injection must "
                           "complete before next LLM request)")

    # ── 按 type 校验必填字段 ──
    required = _TYPE_REQUIRED_FIELD.get(action_type, "")
    if required and not str(action_raw.get(required, "")).strip():
        return None, _diag(f"action type {action_type!r} requires field {required!r}")

    # ── 条件 ──
    condition_text = raw.get("if")
    condition = None
    if condition_text:
        try:
            condition = ConditionGroup.parse(str(condition_text))
        except ValueError as e:
            return None, _diag(f"condition parse error: {e}")

    timeout_raw = action_raw.get("timeout", 60)
    try:
        timeout = int(timeout_raw)
    except (TypeError, ValueError):
        timeout = 60

    action = Action(
        type=action_type,
        command=str(action_raw.get("command", "")),
        message=str(action_raw.get("message", "")),
        url=str(action_raw.get("url", "")),
        method=str(action_raw.get("method", "POST")),
        body=str(action_raw.get("body", "")),
        headers={str(k): str(v) for k, v in (action_raw.get("headers") or {}).items()},
        prompt=str(action_raw.get("prompt", "")),
        timeout=timeout,
    )

    hook = Hook(
        id=hook_id,
        event=event,
        action=action,
        condition=condition,
        reject=reject,
        once=bool(raw.get("once", False)),
        async_exec=async_exec,
        source=source,
    )
    return hook, ""


class HookEngine:
    """加载校验 → 分桶 → observe / gate 双通道派发(§6/§6.1)。"""

    def __init__(self, hooks: list[Hook], work_dir: Path) -> None:
        self._hooks = hooks
        self._work_dir = work_dir
        self._buckets: dict[str, list[Hook]] = {}
        for hook in hooks:
            self._buckets.setdefault(hook.event, []).append(hook)
        self._notifications: list[HookNotification] = []
        self._prompt_messages: list[str] = []

    # ── 加载入口 ──────────────────────────────────────────────────

    @classmethod
    def from_config(
        cls, raw_hooks: list[dict], work_dir: Path
    ) -> tuple["HookEngine", list[str]]:
        """原始映射列表 → 引擎 + 定位诊断列表(空 = 全部通过)。

        单条非法 → 丢弃该条 + 诊断,不影响其余;引擎照常就绪(§6 fail 口径)。
        """
        hooks: list[Hook] = []
        diagnostics: list[str] = []
        for i, raw in enumerate(raw_hooks):
            if not isinstance(raw, dict):
                diagnostics.append(f"[hooks] #{i}: entry is not a mapping, skipped")
                continue
            hook, diag = _validate(raw, str(raw.get("__source__", "")), i)
            if hook is not None:
                hooks.append(hook)
            if diag:
                diagnostics.append(diag)
        return cls(hooks, work_dir), diagnostics

    @property
    def hooks(self) -> list[Hook]:
        return list(self._hooks)

    # ── 匹配 ──────────────────────────────────────────────────────

    def _find_matching(self, event: str, ctx: HookContext) -> list[Hook]:
        matched: list[Hook] = []
        for hook in self._buckets.get(event, []):
            if not hook.should_run():
                continue
            if hook.condition is not None and not hook.condition.evaluate(ctx):
                continue
            matched.append(hook)
        return matched

    # ── observe 通道:非拦截事件 ──────────────────────────────────

    async def observe(self, event: str, ctx: HookContext) -> None:
        """非拦截事件派发:执行完就完,失败只记日志不中断主流程(§6 错误兜底)。"""
        for hook in self._find_matching(event, ctx):
            hook.mark_executed()
            if hook.async_exec:
                asyncio.ensure_future(self._run_single(hook, ctx))
            else:
                await self._run_single(hook, ctx)

    async def _run_single(self, hook: Hook, ctx: HookContext) -> None:
        try:
            result = await execute_action(hook.action, ctx, self._work_dir)
            if hook.action.type == "prompt" and result.success:
                self._prompt_messages.append(result.output)
            self._notifications.append(
                HookNotification(
                    hook_id=hook.id, event=hook.event,
                    output=result.output, success=result.success,
                )
            )
            if not result.success:
                log.warning("Hook '%s' action failed: %s", hook.id, result.output)
        except Exception as e:
            log.warning("Hook '%s' execution error: %s", hook.id, e)
            self._notifications.append(
                HookNotification(
                    hook_id=hook.id, event=hook.event,
                    output=str(e), success=False,
                )
            )

    # ── gate 通道:拦截事件 ────────────────────────────────────────

    async def gate(self, ctx: HookContext) -> ToolRejectedError | None:
        """pre_tool_use / permission_request 裁决:reject 即短路返回哨兵(§6.1)。

        声明了 reject 的 hook 自身执行失败也照样否决(fail-closed,plan §1 提议值)。
        """
        for hook in self._find_matching(ctx.event_name, ctx):
            hook.mark_executed()
            try:
                result = await execute_action(hook.action, ctx, self._work_dir)
                self._notifications.append(
                    HookNotification(
                        hook_id=hook.id, event=ctx.event_name,
                        output=result.output, success=result.success,
                    )
                )
                if hook.reject:
                    return ToolRejectedError(
                        tool=ctx.tool_name, reason=result.output, hook_id=hook.id,
                    )
            except Exception as e:
                log.warning("Hook '%s' execution error: %s", hook.id, e)
                if hook.reject:
                    return ToolRejectedError(
                        tool=ctx.tool_name, reason=str(e), hook_id=hook.id,
                    )
        return None

    # ── 抽干 ──────────────────────────────────────────────────────

    def drain_notifications(self) -> list[HookNotification]:
        out = list(self._notifications)
        self._notifications.clear()
        return out

    def get_prompt_messages(self) -> list[str]:
        out = list(self._prompt_messages)
        self._prompt_messages.clear()
        return out
