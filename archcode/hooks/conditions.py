"""Hook 条件(if)解析器与求值(hooks-design §4)。

把 YAML 里的条件字符串(如 ``tool == "Bash" && args.command =~ /rm\\s+-rf/``)
在**加载期**解析成 :class:`ConditionGroup`(三元组结构 + 编译好的正则),
触发期只做 取数(get_field) + 比较,零解析成本。

- 支持 ``==`` ``!=``(精确/反向)与 ``=~``(正则) ``~=``(glob) 四操作符;
- ``&&``(且)/ ``||``(或)组合,**不可混用**(混用 → ValueError,避免引入优先级);
- 未知字段/未知操作符 → 空串/False,不报错(容错,§4.1)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from archcode.hooks.models import HookContext

_OPERATORS = ("==", "!=", "=~", "~=")


def _glob_to_regex(pattern: str) -> re.Pattern:
    """gitignore 风格 glob → 正则。

    ``*`` 匹配任意字符但不跨目录分隔符;``**`` 匹配任意层级(可跨 /);
    ``?`` 匹配单个字符。示例:``*.py`` 只匹配单层,``src/**/*.go`` 匹配任意深度。
    """
    out = ["^"]
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
                if i < n and pattern[i] == "/":  # "**/" 目录前缀
                    out.append("(?:/)?")
                    i += 1
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def _unquote(value: str, op: str) -> str:
    """去掉值的包裹:精确/glob 用引号,正则用 /…/。"""
    value = value.strip()
    if op == "=~":
        if len(value) >= 2 and value.startswith("/") and value.endswith("/"):
            return value[1:-1]
        return value
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


@dataclass
class _Condition:
    """一个三元组:field(取哪个字段) + operator(怎么比) + value(配置常量)。"""

    field: str
    operator: str
    value: str = ""
    regex: re.Pattern | None = None      # op == "=~" 时编译
    glob_re: re.Pattern | None = None    # op == "~=" 时编译

    def matches(self, actual: str) -> bool:
        if self.operator == "==":
            return actual == self.value
        if self.operator == "!=":
            return actual != self.value
        if self.operator == "=~":
            return self.regex is not None and bool(self.regex.search(actual))
        if self.operator == "~=":
            return self.glob_re is not None and bool(self.glob_re.match(actual))
        return False  # 未知操作符 → False(容错)


class ConditionGroup:
    """一组子条件 + 组合模式(and/or)。加载期 parse,触发期 evaluate。"""

    def __init__(self, conditions: list[_Condition], mode: str) -> None:
        self.conditions = conditions
        self.mode = mode  # "and" | "or"

    @classmethod
    def parse(cls, text: str) -> "ConditionGroup":
        text = (text or "").strip()
        if not text:
            raise ValueError("empty hook condition")

        has_and = "&&" in text
        has_or = "||" in text
        if has_and and has_or:
            raise ValueError(
                "hook condition mixes '&&' and '||' — pick one (use multiple "
                "hooks instead)"
            )
        mode = "and" if has_and else ("or" if has_or else "and")
        sep = "&&" if has_and else "||"

        sub_conditions = [p.strip() for p in text.split(sep) if p.strip()]
        if not sub_conditions:
            raise ValueError(f"hook condition has no sub-conditions: {text!r}")

        conditions: list[_Condition] = []
        for part in sub_conditions:
            conditions.append(cls._parse_triple(part))
        return cls(conditions, mode)

    @staticmethod
    def _parse_triple(part: str) -> _Condition:
        # field 和 operator 是首个/第二个 token,value 是其余(可含空格)
        tokens = part.split(None, 2)
        if len(tokens) < 3:
            raise ValueError(
                f"hook condition sub-expression must be 'field operator value': {part!r}"
            )
        fld, op, raw_value = tokens[0], tokens[1], tokens[2]
        if op not in _OPERATORS:
            raise ValueError(f"unknown hook condition operator: {op!r}")
        value = _unquote(raw_value, op)

        c = _Condition(field=fld, operator=op, value=value)
        if op == "=~":
            try:
                c.regex = re.compile(value)
            except re.error as e:
                raise ValueError(f"invalid regex in hook condition: {value!r}: {e}") from e
        elif op == "~=":
            c.glob_re = _glob_to_regex(value)
        return c

    def evaluate(self, ctx: HookContext) -> bool:
        actual_values = [ctx.get_field(c.field) for c in self.conditions]
        results = [c.matches(actual) for c, actual in zip(self.conditions, actual_values)]
        if self.mode == "and":
            return all(results)
        return any(results)

    def __bool__(self) -> bool:
        return bool(self.conditions)
