"""工具过滤的四道防线(sub-agent-design §4.3)。

漏斗式依次执行:第 0 层 MCP 恒放行 → 第 1 层全局禁用 → 第 2 层自定义收紧
(按来源)→ 第 3 层后台白名单(按是否后台;"保留"语义)→ 第 4 层定义黑/白
名单。系统工具(is_system_tool,如 LoadSkill/ToolSearch)与 MCP 工具恒保留。
返回**新建的** ToolRegistry(装父工具实例的引用,父注册表不动,§4.3 实现形态)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from archcode.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from archcode.agents.models import AgentDef

# 第 1 层:全局禁止清单(硬编码)——调度/对话类工具只在主 agent 手里(§4.3)
ALL_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset({
    "Agent",
    "AskUserQuestion",
    "TaskList",
    "TaskGet",
    "TaskCreate",
    "TaskUpdate",
    "SendMessage",
})

# 第 2 层:自定义 agent(project/user 来源)额外禁止——预留槽,起步与第 1 层同集
# (§4.3 澄清:闸门在、差异化清单待将来有真实候选再填)
CUSTOM_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset(ALL_AGENT_DISALLOWED_TOOLS)

# 第 3 层:后台白名单(硬编码起步清单)——基础读写/搜索/命令 + Skill 基础设施(§4.3/§8.4)
BACKGROUND_ALLOWED_TOOLS: frozenset[str] = frozenset({
    "Bash",
    "ReadFile",
    "WriteFile",
    "EditFile",
    "Glob",
    "Grep",
    "ToolSearch",
    "Skill",
    "LoadSkill",
})

_CUSTOM_SOURCES = {"project", "user", "plugin"}


class SkillDependencyError(Exception):
    """skill fork 的 allowedTools 引用了不存在的工具(§4.7:fail-fast)。"""


def _is_mcp_tool(name: str) -> bool:
    """ArchCode 的 MCP 工具命名:mcp_{server}_{tool}(单下划线,见 mcp/tool_wrapper.py)。"""
    return name.startswith("mcp_")


def resolve_agent_tools(
    parent_registry: ToolRegistry,
    defn: "AgentDef",
    is_background: bool = False,
) -> ToolRegistry:
    """按四道防线为子 agent 过滤出可用工具集(新建注册表,父不动)。"""
    all_tools = {t.name: t for t in parent_registry.list_tools()}

    # 第 0 层:MCP 工具恒放行(先分离,后续层不作用于它们)
    mcp_tools = {n: t for n, t in all_tools.items() if _is_mcp_tool(n)}
    rest = {n: t for n, t in all_tools.items() if n not in mcp_tools}

    # 第 1 层:全局禁止(所有子 agent)
    for name in ALL_AGENT_DISALLOWED_TOOLS:
        rest.pop(name, None)

    # 第 2 层:自定义 agent(project/user/plugin 来源)额外禁止(预留槽)
    if defn.source in _CUSTOM_SOURCES:
        for name in CUSTOM_AGENT_DISALLOWED_TOOLS:
            rest.pop(name, None)

    # 第 3 层:后台白名单(跟"是否后台"走,不跟路径走;fork 恒后台也过,§8.4)
    if is_background:
        rest = {
            n: t
            for n, t in rest.items()
            if n in BACKGROUND_ALLOWED_TOOLS or getattr(t, "is_system_tool", False)
        }

    # 第 4 层:定义黑名单排除 → 白名单取交集
    for name in defn.disallowed_tools:
        rest.pop(name, None)
    if defn.tools:
        allowed = set(defn.tools)
        rest = {n: t for n, t in rest.items() if n in allowed}

    # 系统工具恒注入(is_system_tool:LoadSkill/ToolSearch——嵌套激活与延迟加载的前提)
    for n, t in all_tools.items():
        if getattr(t, "is_system_tool", False) and n not in rest and n not in mcp_tools:
            rest[n] = t

    filtered = ToolRegistry()
    for tool in mcp_tools.values():
        filtered.register(tool)
    for tool in rest.values():
        filtered.register(tool)
    return filtered


def filter_registry_by_allowlist(
    parent_registry: ToolRegistry, allowed: list[str]
) -> ToolRegistry:
    """共享原语:按允许名单过滤注册表(skill fork 的 allowedTools 路径,§4.7/§13)。

    allowed 为空 → 返回原 registry(向后兼容);任一名字缺失 → SkillDependencyError
    (fail-fast);系统工具恒注入。新注册表装父工具实例的引用。
    """
    if not allowed:
        return parent_registry
    all_tools = {t.name: t for t in parent_registry.list_tools()}
    filtered = ToolRegistry()
    missing: list[str] = []
    for name in allowed:
        tool = all_tools.get(name)
        if tool is None:
            missing.append(name)
            continue
        filtered.register(tool)
    if missing:
        raise SkillDependencyError(
            f"以下工具不在当前注册表中: {', '.join(missing)}"
        )
    for tool in all_tools.values():
        if getattr(tool, "is_system_tool", False) and filtered.get(tool.name) is None:
            filtered.register(tool)
    return filtered
