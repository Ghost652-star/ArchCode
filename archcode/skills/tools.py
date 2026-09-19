"""LoadSkill 工具:模型激活 Skill 的入口(skills-design.md 0.2 / 0.3)。

属性契约(0.3):
- category="read" → 走既有只读权限通道,所有模式自动放行、不触发 HITL
- is_concurrency_safe=False → 修改激活集合与钉住消息,禁止进并发批次
- recovery_kind="skill" + recovery_key_arg="name" → agent 的 recovery 登记分流标记(18.7)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from archcode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from archcode.skills.executor import SkillExecutor


class LoadSkillParams(BaseModel):
    name: str = Field(description="要激活的 Skill 名称(见系统提示中的 Skill 目录)")
    args: str = Field(
        default="",
        description="可选:传给 Skill 的参数,替换 SOP 正文中的 $ARGUMENTS 占位符",
    )


class LoadSkillTool(Tool):
    name = "LoadSkill"
    description = (
        "按名称激活一个 Skill:读取它的完整 SOP 并钉入上下文,之后的轮次持续可见。"
        "仅当用户请求明确匹配某个已列出的 Skill 时调用;不要重复激活已激活的 Skill。"
    )
    params_model = LoadSkillParams
    category = "read"
    is_concurrency_safe = False
    is_system_tool = True  # 系统工具:豁免 allowedTools 边界(0.6),嵌套激活的前提
    recovery_kind = "skill"
    recovery_key_arg = "name"

    def __init__(self) -> None:
        self._executor: "SkillExecutor | None" = None

    def set_executor(self, executor: "SkillExecutor") -> None:
        """启动接线:app/__main__ 在创建 executor 后调用。"""
        self._executor = executor

    async def execute(self, params: BaseModel) -> ToolResult:
        if self._executor is None:
            return ToolResult(
                output="Error: LoadSkill not properly initialized",
                is_error=True,
            )
        assert isinstance(params, LoadSkillParams)
        message = self._executor.activate(params.name, params.args)
        return ToolResult(output=message, is_error=message.startswith("Error"))
