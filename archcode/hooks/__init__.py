"""Hook 系统:用户在 agent 生命周期事件上声明式配置动作(hooks-design.md)。

三要素 event / condition / action,条件 DSL 见 conditions.py,四执行器见
executors.py,引擎见 engine.py。数据流(§6.1):

    config.yaml(hooks:) → HookEngine.from_config → observe / gate 双通道
    gate 拒绝 → ToolRejectedError → 错误 tool_result 回 LLM
"""

from archcode.hooks.conditions import ConditionGroup
from archcode.hooks.engine import HookEngine
from archcode.hooks.models import (
    GATE_EVENTS,
    VALID_EVENTS,
    VALID_EXECUTOR_TYPES,
    Action,
    Hook,
    HookContext,
    HookNotification,
    HookResult,
    ToolRejectedError,
)

__all__ = [
    "Action",
    "ConditionGroup",
    "GATE_EVENTS",
    "Hook",
    "HookContext",
    "HookEngine",
    "HookNotification",
    "HookResult",
    "ToolRejectedError",
    "VALID_EVENTS",
    "VALID_EXECUTOR_TYPES",
]
