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
        """激活入口:LoadSkill 工具与 /skill-name 命令共用,按 mode 分流。"""
        manifest = self.loader.get(name)
        if manifest is None:
            available = ", ".join(sorted(self.loader.manifests())) or "(无)"
            return f"Error: unknown skill '{name}'. Available skills: {available}"
        if manifest.mode == "fork":
            return self._activate_fork(manifest, args)
        return self._activate_inline(manifest, args, conversation)

    def _activate_fork(self, manifest: Any, args: str) -> str:
        """fork 模式:创建一个子 agent 执行该 skill(sub-agent-design §13)。

        - 对话按 manifest.context 档位构建(§5.4):**不是空白对话**——
          recent(默认)携带近期纯对话文本,none 才接近空白;
        - 任务 = 渲染后的 SOP(追加到对话末尾);
        - 工具集 = allowedTools 过滤注册表(缺名 fail-fast,§4.7);
        - 无条件后台(§8),结果经 <task-notification> 回传;
        - 子 agent 标记 is_fork=True(防递归家族,§5.5)。
        """
        task_manager = getattr(self, "task_manager", None)
        if task_manager is None:
            return (
                f"Error: skill '{manifest.name}' 是 fork 模式——后台任务管理器未接线"
                f"(agents/ 系统未启用),当前仅支持 inline 模式。"
            )
        agent = self.agent
        parent_conv = getattr(agent, "_current_conversation", None)
        if parent_conv is None:
            return f"Error: skill '{manifest.name}' fork 失败:当前没有活动对话。"

        body = self.loader.read_body(manifest.name)
        if body is None:
            return f"Error: skill '{manifest.name}' 的正文读取失败,激活已取消。"
        rendered = substitute_arguments(body, args)

        from archcode.agents.tool_filter import (
            SkillDependencyError,
            filter_registry_by_allowlist,
        )

        # 目录型 skill:先注册 tool.json 专属工具(§4.7 序列第 1 步)——
        # allowedTools 可引用它们;随后过滤。失败回滚,与 inline 同款事务(0.5)。
        owned: list[str] = []
        registry = agent._tool_registry
        owner = f"skill:{manifest.name}"
        if manifest.is_directory and manifest.skill_dir is not None and registry is not None:
            from archcode.skills.directory import register_skill_tools

            owned, diagnostics = register_skill_tools(
                manifest.skill_dir, registry, owner=owner
            )
            if diagnostics:
                self.loader.diagnostics.extend(diagnostics)

        try:
            filtered_registry = filter_registry_by_allowlist(
                registry, list(manifest.allowed_tools)
            )
        except SkillDependencyError as exc:
            if owned and registry is not None:
                registry.unregister_owner(owner)  # 原子事务:回滚已注册的专属工具
            return f"Error: skill '{manifest.name}' 声明的工具不存在: {exc}。激活已取消。"

        fork_conv = self._build_fork_context(manifest.context, parent_conv)
        fork_conv.add_user(rendered)  # 渲染后的 SOP 作任务(对话末尾)

        from archcode.agent import Agent  # 延迟导入避免循环
        from archcode.agents.task_manager import SubAgentRunner
        from archcode.permissions import PermissionChecker, PermissionMode
        from archcode.permissions.sandbox import PathSandbox

        checker = PermissionChecker(
            mode=PermissionMode.DONT_ASK,  # fork worker(§10;工具集已被 allowedTools 锁死)
            sandbox=PathSandbox(agent._work_dir) if agent._work_dir else None,
        )
        sub = Agent(
            client=agent._client,
            system_prompt=agent._system_prompt,  # fork 继承父 system_prompt(§5.1)
            tool_registry=filtered_registry,
            permission_checker=checker,
            max_iterations=agent._max_iterations,
            work_dir=agent._work_dir,
        )
        sub._hook_engine = agent._hook_engine
        sub._skill_loader = agent._skill_loader
        sub.is_fork = True

        runner = SubAgentRunner(sub, fork_conv, inject_task=False)
        task_id = task_manager.launch(runner, task="", name=manifest.name)
        return (
            f"Skill '{manifest.name}' 已以后台子 agent 启动(fork 模式)。\n"
            f"Task ID: {task_id}\n"
            f"完成后将经 <task-notification> 回传结果,请勿轮询。"
        )

    def _build_fork_context(self, context: str, parent_conv: Any) -> Any:
        """按 context 档位取父对话(§5.4;先行版实现)。

        - none:空对话(只有任务);
        - recent(默认):从尾往前取纯对话文本(排除 tool 消息 → 天然
          tool-pair 对齐),按字符预算截取(先行版;token 预算同理);
        - full:先行版按加宽的 recent 预算实现,压缩摘要复用随 compactor
          接线补(§12 已记录)。
        """
        from archcode.agents.fork import FORK_BOILERPLATE_TAG  # noqa: F401
        from archcode.conversation.manager import ConversationManager
        from archcode.conversation.models import estimate_tokens

        if context == "none":
            return ConversationManager()  # 空对话:只有任务(§5.4)
        budget = 12000 if context == "recent" else 36000  # 字符 ≈ token×3.5
        history = parent_conv.history
        taken: list = []
        used = 0
        for message in reversed(history):
            if not message.content or message.tool_uses or message.tool_results:
                continue  # 排除工具消息:截取段内无 tool-pair,不会产生孤儿
            if FORK_BOILERPLATE_TAG in message.content:
                continue  # 保险:不把 fork 标记带进新子对话
            cost = estimate_tokens([message]) * 4  # ≈字符数
            if used + cost > budget:
                break
            taken.append(message)
            used += cost
        fork_conv = ConversationManager()
        fork_conv.history = list(reversed(taken))
        return fork_conv

    def _activate_inline(
        self,
        manifest: Any,
        args: str,
        conversation: "ConversationManager | None",
    ) -> str:
        """inline 激活事务。"""
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
