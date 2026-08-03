"""ArchCode 的权限模式定义。

4 个 mode:
  default  - 写和 bash 都要问
  accept   - 写自动过,bash 仍问
  bypass   - 写和 bash 都自动过
  plan     - 只读 + 写 plan 文件,bash 拒绝

mode 切换**不注入提示词**(跟 plan_mode 不同)——LLM 不需要知道
当前 mode,模式只是执行层决策,改 LLM 工具调用的过不过。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal


DecisionEffect = Literal["allow", "deny", "ask"]


class PermissionMode(str, Enum):
    DEFAULT = "default"
    PLAN = "plan"
    ACCEPT = "accept"
    BYPASS = "bypass"


# 4 mode × 3 category 的判定矩阵
_MATRIX: dict[PermissionMode, dict[str, DecisionEffect]] = {
    PermissionMode.DEFAULT: {
        "read": "allow",
        "write": "ask",
        "command": "ask",
    },
    PermissionMode.ACCEPT: {
        "read": "allow",
        "write": "allow",
        "command": "ask",
    },
    PermissionMode.BYPASS: {
        "read": "allow",
        "write": "allow",
        "command": "allow",
    },
    # PLAN 模式不进普通矩阵——有专用路径(在 checker.py)
}


def mode_decide(mode: PermissionMode, category: str) -> DecisionEffect:
    """根据当前 mode 和 tool 类别,返回 'allow' / 'deny' / 'ask'。

    适用于 non-PLAN 模式。PLAN 模式应该走 checker 里的专用路径,不调这个。
    """
    return _MATRIX[mode][category]
