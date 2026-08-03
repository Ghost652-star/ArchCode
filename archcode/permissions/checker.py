"""权限判定引擎 —— 4 层判定 + plan 模式专用路径。

调用链：
    Agent._execute_tool → PermissionChecker.check(tool_name, category, arguments)
    → Decision(effect, reason)

effect="deny" 时，Agent 把 reason 包装进 ToolResult 返回给 LLM，
让 LLM 看到被拦截原因并自动调整行为（而不是直接弹窗给用户）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from archcode.permissions.dangerous import DangerousCommandDetector, is_safe_command
from archcode.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from archcode.permissions.sandbox import PathSandbox


# ---------------------------------------------------------------------------
# Plan 模式下允许放行的特殊工具
# ---------------------------------------------------------------------------

_PLAN_MODE_ALLOWED_TOOLS = frozenset({
    "Agent",
    "AskUserQuestion",
    "ExitPlanMode",
})

# 工具参数中承载"操作对象"的字段名（用于提取 content）
_CONTENT_FIELDS: dict[str, str] = {
    "Bash": "command",
    "ReadFile": "file_path",
    "WriteFile": "file_path",
    "EditFile": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
}


def extract_content(tool_name: str, arguments: dict[str, Any]) -> str:
    """从工具参数中提取"操作对象"字符串（路径或命令）。"""
    field = _CONTENT_FIELDS.get(tool_name)
    if field is None:
        return ""
    return str(arguments.get(field, ""))


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    effect: DecisionEffect  # allow / deny / ask
    reason: str = ""


# ---------------------------------------------------------------------------
# PermissionChecker
# ---------------------------------------------------------------------------


class PermissionChecker:
    """4 层判定 + plan 模式专用路径的权限引擎。

    Layers:
        0. Plan 模式专用路径（允许 plan 相关工具和 plan 文件写入）
        1. 安全命令自动放行 + 危险命令黑名单拒绝
        2. 路径沙箱（文件工具必须在白名单目录内）
        3. 权限模式矩阵兜底（mode_decide）
        4. HITL —— 返回 "ask" 触发人工确认

    Plan mode 不进普通 mode 矩阵，有独立判定逻辑。
    """

    def __init__(
        self,
        detector: DangerousCommandDetector | None = None,
        sandbox: PathSandbox | None = None,
        mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> None:
        self.detector = detector or DangerousCommandDetector()
        self.sandbox = sandbox
        self.mode = mode
        self.plan_file_path: str = ""

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def check(
        self,
        tool_name: str,
        category: str,
        arguments: dict[str, Any] | None = None,
    ) -> Decision:
        """判定工具调用是否允许执行。

        Args:
            tool_name: 工具名（"Bash", "ReadFile", ...）
            category: 工具类别（"read", "write", "command"）
            arguments: 工具参数字典

        Returns:
            Decision with effect="allow" / "deny" / "ask"
        """
        args = arguments or {}
        content = extract_content(tool_name, args)

        # ── Layer 0: Plan 模式专用路径 ─────────────────────────────
        if self.mode == PermissionMode.PLAN:
            return self._check_plan_mode(tool_name, category, content)

        # ── Layer 1: 安全命令放行 / 危险命令拒绝 ──────────────────
        if category == "command" and content:
            if is_safe_command(content):
                return Decision(effect="allow", reason="安全只读命令，自动放行")

            hit, reason = self.detector.detect(content)
            if hit:
                return Decision(
                    effect="deny",
                    reason=f"危险命令拦截: {reason}",
                )

        # ── Layer 2: 路径沙箱 ─────────────────────────────────────
        if category in ("read", "write") and content and self.sandbox is not None:
            ok, reason = self.sandbox.check(content)
            if not ok:
                return Decision(
                    effect="deny",
                    reason=f"路径沙箱拦截: {reason}",
                )

        # ── Layer 3: 权限模式矩阵 ─────────────────────────────────
        effect = mode_decide(self.mode, category)
        if effect in ("allow", "deny"):
            return Decision(
                effect=effect,
                reason=f"权限模式 {self.mode.value} → {effect}",
            )

        # ── Layer 4: HITL 人工确认 ────────────────────────────────
        return Decision(effect="ask", reason="需要用户确认此操作")

    # ------------------------------------------------------------------
    # Plan mode
    # ------------------------------------------------------------------

    def _check_plan_mode(
        self, tool_name: str, category: str, content: str
    ) -> Decision:
        """Plan 模式专用判定：只允许读取 + 写 plan 文件 + 特定工具。"""
        # 特殊工具放行（AskUserQuestion, ExitPlanMode 等）
        if tool_name in _PLAN_MODE_ALLOWED_TOOLS:
            return Decision(effect="allow", reason="Plan mode: 特殊工具放行")

        # 写 plan 文件放行
        if tool_name in ("WriteFile", "EditFile") and content:
            if self._is_plan_file(content):
                return Decision(effect="allow", reason="Plan mode: 写入 plan 文件")

        # 只读工具放行
        if category == "read":
            if self.sandbox is not None and content:
                ok, reason = self.sandbox.check(content)
                if not ok:
                    return Decision(effect="deny", reason=f"Plan mode + 沙箱: {reason}")
            return Decision(effect="allow", reason="Plan mode: 只读放行")

        # 其余全部拒绝
        return Decision(
            effect="deny",
            reason=(
                f"Plan mode 已开启: 工具 '{tool_name}' 被拦截。"
                "只允许只读工具和修改 plan 文件。用 /exit-plan 退出 plan mode。"
            ),
        )

    def _is_plan_file(self, target_path: str) -> bool:
        """判断目标路径是否是当前 plan 文件。"""
        import os

        if not self.plan_file_path or not target_path:
            return ".archcode/plans/" in target_path
        try:
            if os.path.abspath(target_path) == os.path.abspath(self.plan_file_path):
                return True
        except Exception:
            pass
        if os.path.basename(target_path) == os.path.basename(self.plan_file_path):
            return True
        return ".archcode/plans/" in target_path
