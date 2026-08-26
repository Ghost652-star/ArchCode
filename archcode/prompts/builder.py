"""ArchCode 的 prompt 组装 + dynamic reminders。

三个职责:
- ``PromptBuilder``:链式组装多条 ``PromptSection``,按 priority 排序输出
- ``build_system_prompt``:静态 system 字段的入口,7 个 section + environment
- ``build_plan_mode_reminder``:动态 system-reminder 文本,plan mode 时每轮重发
"""

from __future__ import annotations

from archcode.prompts.sections import (
    COMMUNICATION_SECTION,
    DOING_TASKS_SECTION,
    EXECUTING_ACTIONS_SECTION,
    IDENTITY_SECTION,
    PromptSection,
    SYSTEM_SECTION,
    USING_TOOLS_SECTION,
    environment_section,
)


class PromptBuilder:
    """链式组装多条 PromptSection,按 priority 排序输出。"""

    def __init__(self) -> None:
        self._sections: list[PromptSection] = []

    def add(self, section: PromptSection) -> "PromptBuilder":
        self._sections.append(section)
        return self

    def build(self) -> str:
        # 排序、空 section 跳过、整体空行 join
        ordered = sorted(self._sections, key=lambda s: s.priority)
        parts = [s.content.strip() for s in ordered if s.content.strip()]
        return "\n\n".join(parts)


def build_system_prompt(
    work_dir: str | None = None,
    extra: str = "",
) -> str:
    """组装完整的 system prompt:7 个 section + environment + 可选 extra。

    Args:
        work_dir: 当前项目工作目录;用于 # Environment 段,也用于 plan reminder。
                  None 时,environment section 仍会生成但工作目录显示 "(unknown)"。
        extra: 配置文件里 system_prompt 字段的自定义追加内容(项目级指令)。

    扩展契约:将来 memory / skills / CLAUDE.md 指令落地时,加 kwarg 时必须:
      1. 默认值 = ""(空字符串),不变现有调用方
      2. 空字符串时跳过对应 section(PromptBuilder 不会加空 content)
      3. 非空时才 ``b.add(PromptSection(name=..., priority=..., content=...))``
      4. priority 排到 70 之后(environment 是 70)
      5. 不要用 list[xxx] 这种"复杂数据"——保持 string 不变,数据拼装在调用方完成
      6. 同步改 system-reminder 路径:新参数如有"每轮变化"的部分,
         让 call site 用 conversation.add_system_reminder 注入,而不是塞进 system 字段
    设计参考:MewCode 的 build_system_prompt 也是用 kwargs+defaults,
    但 MewCode 把 skills/memory/custom_instructions 全塞 system 字段(会破 cache)。
    我们保留这种"未来可能加的 kwargs 形状",但劝阻把它们放进 system 字段。
    详细见 docs/prompts-design.md。

    Returns:
        完整 system prompt 字符串,直接喂给 LLM client。
    """
    b = PromptBuilder()
    b.add(IDENTITY_SECTION)
    b.add(SYSTEM_SECTION)
    b.add(DOING_TASKS_SECTION)
    b.add(EXECUTING_ACTIONS_SECTION)
    b.add(USING_TOOLS_SECTION)
    b.add(COMMUNICATION_SECTION)
    b.add(environment_section(work_dir))
    prompt = b.build()

    if extra.strip():
        prompt = prompt + "\n\n" + extra.strip()
    return prompt


# ---------------------------------------------------------------------------
# Dynamic reminders(走 conversation.add_system_reminder 而不是 system 字段)
# ---------------------------------------------------------------------------


_PLAN_MODE_FULL_REMINDER_INTERVAL = 5


def build_plan_mode_reminder(
    plan_path: str,
    work_dir: str | None,
    *,
    iteration: int = 1,
) -> str:
    """构造 plan mode 的 reminder 文本。

    system-reminder 跟 system prompt 的区别:
    - system prompt 静态,session 启一次,在每轮 API 的 messages 里出现
    - system-reminder 动态,每轮重新发,让 LLM 知道"你现在在什么 mode / 状态"

    调用方把返回值塞进 conversation.add_system_reminder(text)。

    Args:
        plan_path: plan 文件的绝对路径(<work_dir>/.archcode/plans/{slug}.md)
        work_dir: 当前项目工作目录;None 时不显示项目边界提示。
        iteration: 当前 ReAct iteration。第 1、6、11……轮发送完整提醒，
            中间轮次发送简短提醒。

    Returns:
        提醒文本,会被包在 <system-reminder>...</system-reminder> 标签里发出。
    """
    if iteration > 1 and (iteration - 1) % _PLAN_MODE_FULL_REMINDER_INTERVAL:
        return (
            "Plan Mode 仍开启。仅可执行只读操作；只能修改计划文件：\n"
            f"{plan_path}\n"
            "不要修改其他项目文件，也不要执行 Bash。"
            "完整 Plan Mode 工作流见此前提醒。"
        )

    work_dir_block = ""
    if work_dir:
        work_dir_block = (
            f"\n项目工作目录: {work_dir}\n"
            "  这个目录可能有代码(已有项目),也可能是空的(新项目)。\n"
            "  请只在这个目录及其子目录里工作。"
            "\n"
        )

    return (
        "Plan mode 已开启。\n\n"
        "允许的工具: ReadFile、Glob、Grep(都是只读)。\n"
        "WriteFile 和 EditFile 只在目标路径等于下面的 plan 文件时才允许,\n"
        "其他任何写入都会被拒绝。Bash 完全禁用。"
        f"{work_dir_block}\n"
        "根据用户需求 + 项目当前状态,把方案写到下面的 plan 文件:\n"
        "  - 项目已有代码:可读相关文件理解现状,再设计增量改动\n"
        "  - 全新项目 / 新功能:基于用户描述和任何澄清问答直接设计\n"
        "  - 不确定的细节先问用户,别瞎假设\n"
        "完成后告诉用户计划已就绪,让他们运行 `/exit-plan`。\n\n"
        f"Plan file: {plan_path}"
    )
