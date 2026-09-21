"""Hooks 数据模型(hooks-design §2/§5)。

- ``Action`` / ``Hook``:YAML 配置解析后的条目(hook 级 vs action 级字段归属见 §2)。
- ``HookContext``:触发时由调用方(agent/app)现场构造的实况快照,一个对象两个
  取数接口——``get_field`` 供 if 条件求值,``expand`` 供占位符替换(§5)。
- ``ToolRejectedError``:gate 通道的拒绝哨兵(作返回,不作 raise,MewCode 同款)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── 事件与执行器的合法取值(加载校验用,hooks-design §3/§6) ──────────
VALID_EVENTS: frozenset[str] = frozenset(
    {
        "session_start",
        "turn_start",
        "pre_tool_use",
        "permission_request",
        "post_tool_use",
        "post_tool_use_failure",
        "turn_end",
        "session_end",
    }
)

# 持有裁决权的事件:gate 通道;async 禁用、reject 仅此两处有意义
GATE_EVENTS: frozenset[str] = frozenset({"pre_tool_use", "permission_request"})

VALID_EXECUTOR_TYPES: frozenset[str] = frozenset(
    {"command", "prompt", "http", "agent"}
)

# 各执行器类型的必填字段(§6 校验清单第五条)
_TYPE_REQUIRED_FIELD = {
    "command": "command",
    "prompt": "message",
    "http": "url",
    "agent": "prompt",
}


@dataclass
class Action:
    """一条 hook 的动作;四种执行器共用一个"胖数据类",按 type 取用(§5)。"""

    type: str
    command: str = ""
    message: str = ""
    url: str = ""
    method: str = "POST"
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    prompt: str = ""
    timeout: int = 60  # 秒;缺省 60(plan §1 提议值)


@dataclass
class Hook:
    """加载后的单条 hook 规则:配置字段 + 运行时状态(executed)共存(§5)。"""

    id: str
    event: str
    action: Action
    condition: Any = None  # ConditionGroup | None(避免循环 import)
    reject: bool = False
    once: bool = False
    async_exec: bool = False
    executed: bool = False
    source: str = ""  # 来源层:"app" | "project" | "local"(定位诊断用)

    def should_run(self) -> bool:
        """once 且已执行过 → 不再触发(§6 执行控制)。"""
        if self.once and self.executed:
            return False
        return True

    def mark_executed(self) -> None:
        self.executed = True


@dataclass
class HookContext:
    """触发时点的实况快照,由调用方构造后推给引擎(§6 数据流入:参数推入)。

    非工具事件的 tool_name / tool_args 为空,条件求值时对应字段返回空串。
    """

    event_name: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    file_path: str = ""
    message: str = ""
    error: str = ""

    def get_field(self, name: str) -> str:
        """条件求值的取数接口:``tool`` / ``event`` / ``args.<key>``,未知 → ""。

        前缀路由(§4.1 词表):``args.`` 开头则剥前缀后去 tool_args 字典按 key 查。
        翻译层——配置词表的键名与数据类字段名不一致(tool→tool_name),故手写路由,
        不可用 getattr 直查。
        """
        if name == "tool":
            return self.tool_name
        if name == "event":
            return self.event_name
        if name.startswith("args."):
            key = name[5:]
            value = self.tool_args.get(key, "")
            return str(value) if value not in (None, "") else ""
        return ""

    def expand(self, template: str) -> str:
        """占位符替换(§5):执行器起子进程前调用,把命令串里的占位符换成实况值。

        六类占位符:$EVENT / $TOOL_NAME / $FILE_PATH / $MESSAGE / $ERROR /
        $TOOL_ARGS.<key>。**长名优先**替换(正则负向断言防 $FILE_PATH 误伤
        $FILE_PATHS);未定义占位符替换为空串,不报错(容错)。
        """
        import re
        result = template
        # $TOOL_ARGS.<key>(动态,最长最具体,先替换)
        for key in sorted(self.tool_args.keys(), key=len, reverse=True):
            pattern = re.escape(f"$TOOL_ARGS.{key}") + r"(?![A-Za-z0-9_])"
            result = re.sub(pattern, str(self.tool_args[key]), result)
        # 固定占位符,长名优先,负向断言防前缀误伤
        for name, value in (
            ("$TOOL_NAME", self.tool_name),
            ("$FILE_PATH", self.file_path),
            ("$MESSAGE", self.message),
            ("$ERROR", self.error),
            ("$EVENT", self.event_name),
        ):
            result = re.sub(re.escape(name) + r"(?![A-Za-z0-9_])", value, result)
        return result


@dataclass
class HookResult:
    """执行器返回值:output = 命令/动作输出;success = 执行是否成功(退出码 0)。"""

    output: str = ""
    success: bool = True


@dataclass
class HookNotification:
    """引擎侧的观察记录(触发/否决/超时/结果),供 TUI 抽干展示(§6 观测)。"""

    hook_id: str
    event: str
    output: str
    success: bool


class ToolRejectedError(Exception):
    """gate 通道的拒绝结果(MewCode 同款:作返回哨兵,不作 raise)。

    引擎 gate() 返回它表示该工具调用被拒;调用方 agent 捕获后把 reason 作为错误
    tool_result 回给 LLM,并跳过该次执行。
    """

    def __init__(self, tool: str, reason: str, hook_id: str) -> None:
        self.tool = tool
        self.reason = reason
        self.hook_id = hook_id
        super().__init__(
            f"Tool '{tool}' rejected by hook '{hook_id}': {reason}"
        )
