"""Business handlers for ArchCode's built-in Slash Commands.

Handlers only express command behaviour through CommandContext and CommandUI.
The Textual App supplies the concrete UI implementation.
"""

from __future__ import annotations

from archcode.commands.core import CommandContext, CommandSpec


REVIEW_PROMPT = (
    "请审查当前工作区的代码变更。重点关注逻辑错误、安全问题、"
    "性能问题和代码风格。"
)
MEMORY_TYPES = frozenset({"user", "feedback", "project", "reference"})


async def handle_help(context: CommandContext) -> None:
    name = context.raw_args.strip().lstrip("/").lower()
    if not name:
        rows = ["可用命令："]
        rows.extend(
            f"  {spec.usage:<28} {spec.description}"
            for spec in context.registry.visible_commands()
        )
        rows.append("输入 /help <命令> 查看详细用法。")
        context.ui.show_system("\n".join(rows))
        return
    spec = context.registry.find(name)
    if spec is None:
        context.ui.show_system(f"未找到命令: /{name}\n输入 /help 查看可用命令。")
        return
    detail = f"{spec.usage}\n{spec.description}"
    if spec.argument_hint:
        detail += f"\n{spec.argument_hint}"
    context.ui.show_system(detail)


async def handle_status(context: CommandContext) -> None:
    context.ui.show_system(context.ui.status_text())


async def handle_compact(context: CommandContext) -> None:
    await context.ui.run_manual_compact(context.raw_args)


async def handle_clear(context: CommandContext) -> None:
    await context.ui.clear_to_new_session()


async def handle_plan(context: CommandContext) -> None:
    await context.ui.toggle_plan_mode(context.raw_args)


async def handle_permission(context: CommandContext) -> None:
    await context.ui.configure_permission(context.raw_args)


async def handle_review(context: CommandContext) -> None:
    prompt = REVIEW_PROMPT
    if context.raw_args.strip():
        prompt += f"\n\n额外关注：{context.raw_args.strip()}"
    await context.ui.run_agent_task(prompt)


async def handle_session(context: CommandContext) -> None:
    args = context.raw_args.split()
    manager = context.session_manager
    session = context.session
    if manager is None:
        context.ui.show_system("当前运行不支持会话持久化。")
        return
    if not args:
        session_id = getattr(session, "session_id", "(none)")
        count = getattr(getattr(session, "meta", None), "message_count", 0)
        context.ui.show_system(f"当前会话：{session_id}\n消息数：{count}")
        return
    command = args[0].lower()
    if command == "list" and len(args) == 1:
        metas = manager.list_sessions()
        if not metas:
            context.ui.show_system("没有可恢复的历史会话。")
            return
        context.ui.show_system(
            "历史会话：\n" + "\n".join(
                f"  {meta.id}  ·  {meta.title or '未命名'}  ·  {meta.message_count} 条"
                for meta in metas
            )
        )
        return
    if command == "new" and len(args) == 1:
        await context.ui.clear_to_new_session()
        return
    if command == "resume":
        if len(args) != 2:
            context.ui.show_system("用法：/session resume <id>")
            return
        await context.ui.resume_session(args[1])
        return
    if command == "delete":
        if len(args) < 2:
            context.ui.show_system("用法：/session delete <id> --confirm")
            return
        target = args[1]
        if "--confirm" not in args[2:]:
            context.ui.show_system(
                f"将删除会话：{target}\n确认执行：/session delete {target} --confirm"
            )
            return
        if target == getattr(session, "session_id", None):
            context.ui.show_system("不能删除当前活跃会话。请先 /session new 或恢复其他会话。")
            return
        if manager.delete(target):
            context.ui.show_system(f"已删除会话：{target}")
        else:
            context.ui.show_system(f"未找到会话：{target}")
        return
    context.ui.show_system(
        "用法：/session [list|new|resume <id>|delete <id> --confirm]"
    )


async def handle_memory(context: CommandContext) -> None:
    manager = context.memory_manager
    if manager is None:
        context.ui.show_system("当前运行不支持长期记忆。")
        return
    parts = context.raw_args.split(maxsplit=2)
    command = parts[0].lower() if parts else "list"
    if command == "list" and len(parts) == 1:
        user_entries = manager.list_entries("user")
        project_entries = manager.list_entries("project")
        lines = [f"长期记忆：user {len(user_entries)} 条，project {len(project_entries)} 条"]
        for scope, entries in (("user", user_entries), ("project", project_entries)):
            lines.extend(f"  [{scope}] {entry.name} — {entry.description}" for entry in entries)
        context.ui.show_system("\n".join(lines))
        return
    if command == "add":
        if len(parts) != 3 or parts[1].lower() not in MEMORY_TYPES:
            context.ui.show_system("用法：/memory add <user|feedback|project|reference> <内容>")
            return
        if await manager.add_manual(parts[1].lower(), parts[2]):
            context.ui.show_system("已添加长期记忆。")
        else:
            context.ui.show_system("记忆未写入：类型、内容或占位文本无效。")
        return
    if command == "clear":
        if len(parts) < 2 or parts[1].lower() not in {"user", "project", "all"}:
            context.ui.show_system("用法：/memory clear <user|project|all> --confirm")
            return
        scope = parts[1].lower()
        if len(parts) != 3 or parts[2] != "--confirm":
            context.ui.show_system(
                f"将清除 {scope} 记忆。确认执行：/memory clear {scope} --confirm"
            )
            return
        scopes = ("user", "project") if scope == "all" else (scope,)
        removed = sum([await manager.clear_scope(item) for item in scopes])
        context.ui.show_system(f"已清除 {removed} 条长期记忆。")
        return
    context.ui.show_system(
        "用法：/memory [list|add <type> <内容>|clear <user|project|all> --confirm]"
    )


def built_in_command_specs() -> list[CommandSpec]:
    """Return the startup registry for commands implemented in this scope."""

    return [
        CommandSpec("help", "显示可用命令及详细用法。", "/help [命令]", handle_help),
        CommandSpec("status", "显示当前运行状态。", "/status", handle_status),
        CommandSpec("compact", "压缩当前会话上下文。", "/compact [额外关注点]", handle_compact),
        CommandSpec("clear", "新建并切换至干净会话。", "/clear", handle_clear),
        CommandSpec("plan", "进入或退出 Plan Mode。", "/plan [任务]", handle_plan),
        CommandSpec("permission", "查看或切换权限模式。", "/permission [mode <模式>]", handle_permission),
        CommandSpec("session", "管理持久化会话。", "/session [子命令]", handle_session),
        CommandSpec("memory", "查看和管理长期记忆。", "/memory [子命令]", handle_memory),
        CommandSpec("review", "审查当前代码变更。", "/review [额外关注点]", handle_review),
    ]
