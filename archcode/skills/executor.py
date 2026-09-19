"""SkillExecutor:激活事务(skills-design.md 0.2 伪代码 + 0.6 allowedTools 校验)。

流程:catalog 查询 → allowedTools 存在性校验(fail-fast) → 读正文 →
$ARGUMENTS 渲染 → 写激活集合 → 事件式重钉 → RecoveryState 登记。
tool_result 不回吐正文(正文已钉进激活消息)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from archcode.skills.loader import SkillLoader, substitute_arguments

if TYPE_CHECKING:
    from archcode.agent import Agent
    from archcode.conversation.manager import ConversationManager
    from archcode.context.recovery import RecoveryState


class SkillExecutor:
    """模型 LoadSkill 工具与 /skill-name 命令共用的激活入口。"""

    def __init__(
        self,
        agent: "Agent",
        loader: SkillLoader,
        recovery: "RecoveryState | None" = None,
    ) -> None:
        self.agent = agent
        self.loader = loader
        self.recovery = recovery

    def activate(
        self,
        name: str,
        args: str = "",
        conversation: "ConversationManager | None" = None,
    ) -> str:
        """激活入口:LoadSkill 工具与 /skill-name 命令共用,按 mode 分流(5.1/5.3)。"""
        manifest = self.loader.get(name)
        if manifest is None:
            available = ", ".join(sorted(self.loader.manifests())) or "(无)"
            return f"Error: unknown skill '{name}'. Available skills: {available}"
        if manifest.mode == "fork":
            return self._activate_fork(manifest)
        return self._activate_inline(manifest, args, conversation)

    def _activate_fork(self, manifest: Any) -> str:
        """预留:fork 执行随 agents/ 子智能体系统落地后实现(skills-design.md 5.3)。

        届时本方法内部调 AgentManager / DelegateTask 链创建独立子会话,并按
        manifest.context 档位构建上下文(full=摘要 / recent=近期消息 / none=不携带,5.4)。
        当前返回明确的暂不支持提示,不静默降级为 inline。
        """
        return (
            f"Error: skill '{manifest.name}' 是 fork 模式——独立子会话执行将随"
            f"子智能体系统(agents/)落地后支持,当前版本仅支持 inline 模式。"
        )

    def _activate_inline(
        self,
        manifest: Any,
        args: str,
        conversation: "ConversationManager | None",
    ) -> str:
        """inline 激活事务(= 教程的 execute_inline + 我们的三项增强)。"""
        name = manifest.name
        registry = getattr(self.agent, "_tool_registry", None)
        owner = f"skill:{name}"

        # 目录型 Skill:先注册自有专属工具(0.5)——allowedTools 可引用它们
        owned: list[str] = []
        if manifest.is_directory and manifest.skill_dir is not None and registry is not None:
            from archcode.skills.directory import register_skill_tools

            owned, diagnostics = register_skill_tools(
                manifest.skill_dir, registry, owner=owner
            )
            if diagnostics:
                self.loader.diagnostics.extend(diagnostics)

        # allowedTools 存在性校验 fail-fast(0.6):缺失即激活失败,绝不留给 agent 中途发现
        missing = self._missing_allowed_tools(manifest, registry)
        if missing:
            if owned and registry is not None:
                registry.unregister_owner(owner)  # 原子事务:回滚已注册的专属工具
            return (
                f"Error: skill '{name}' 声明的工具在当前环境不存在: "
                f"{', '.join(missing)}。激活已取消。"
            )

        body = self.loader.read_body(name)
        if body is None:
            if owned and registry is not None:
                registry.unregister_owner(owner)  # 同样回滚
            return f"Error: skill '{name}' 的正文读取失败,激活已取消。"
        rendered = substitute_arguments(body, args)

        # 钉住内容前缀 Skill 根目录:SOP 里的相对引用(scripts/xxx、references/xxx)
        # 靠它才能解析成可执行的绝对路径(Bash 的 cwd 是 work_dir,不是 Skill 目录)
        root = manifest.skill_dir or manifest.path.parent
        rendered = f"[Skill 根目录: {root}]\n\n{rendered}"

        # 作者提示:传了参数但模板没有 $ARGUMENTS 占位符 → 参数未嵌入 SOP。
        # 参数仍会作为任务文本进入对话,但 SOP 内插缺失几乎肯定是作者忘了写占位符。
        if args.strip() and "$ARGUMENTS" not in body:
            self.loader.diagnostics.append(
                f"[skills] '{name}':传入了参数但正文没有 $ARGUMENTS 占位符,"
                f"参数未嵌入 SOP(仅出现在对话中)"
            )

        already = name in self.agent.active_skills
        self.agent.activate_skill(name, rendered, conversation=conversation)
        if self.recovery is not None:
            self.recovery.record_skill_invocation(
                name,
                rendered,
                mode="inline",
                template_hash=manifest.checksum,
            )
        prefix = "Skill already active: " if already else "Skill activated: "
        return f"{prefix}{name}. SOP 已钉入上下文。"

    @staticmethod
    def _missing_allowed_tools(manifest: Any, registry: Any) -> list[str]:
        """返回 allowedTools 中在 registry 里不存在的工具名(deferred 的算存在)。"""
        if not manifest.allowed_tools or registry is None:
            return []
        return [
            tool_name
            for tool_name in manifest.allowed_tools
            if registry.get(tool_name) is None
        ]
