"""Hook 动作执行器:四种类型,command 先行完整实现(hooks-design §5)。

- ``execute_action(action, ctx, work_dir)`` 是引擎的唯一入口,按 type 分派;
- command:引擎作为父进程起 shell 子进程(与工具系统无关,不经过
  ToolRegistry / _execute_tool / PermissionChecker——否则 pre_tool_use hook 调
  Bash 会自我递归)。stdout / stderr 分离捕获;超时由引擎计时,到点 kill;
- prompt:返回文本(注入落点由 engine / deferred #3 裁决);
- http / agent:接口位,本期返回"未实现"诊断。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from archcode.hooks.models import Action, HookContext, HookResult

_EXECUTOR_MAP = {}  # 填在文件尾部(type → coroutine)


async def execute_command(
    action: Action, ctx: HookContext, work_dir: Path
) -> HookResult:
    """shell 子进程执行;超时 kill;退出码 0 = success(§5 执行机制)。"""
    command = ctx.expand(action.command)
    timeout = max(action.timeout, 1)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(work_dir),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return HookResult(
                output=f"Command timed out after {timeout}s: {command}",
                success=False,
            )
        parts: list[str] = []
        if stdout:
            parts.append(stdout.decode(errors="replace").strip())
        if stderr:
            parts.append(stderr.decode(errors="replace").strip())
        output = "\n".join(parts) if parts else ""
        return HookResult(output=output, success=proc.returncode == 0)
    except Exception as e:
        return HookResult(output=f"Command execution error: {e}", success=False)


async def execute_prompt(
    action: Action, ctx: HookContext, work_dir: Path
) -> HookResult:
    """prompt 执行器:只产出文本,注入落点由引擎暂存(deferred #3 裁决后接线)。"""
    message = ctx.expand(action.message)
    return HookResult(output=message, success=True)


async def execute_http(
    action: Action, ctx: HookContext, work_dir: Path
) -> HookResult:
    """http 执行器:接口位(机制记录于设计 §5,实现顺序实现阶段定)。"""
    return HookResult(
        output="http executor not yet implemented", success=False,
    )


async def execute_agent(
    action: Action, ctx: HookContext, work_dir: Path
) -> HookResult:
    """agent 执行器:占位 stub——接口先留,agents/ 子系统设计完成后对接(§5)。"""
    return HookResult(
        output="agent executor not yet implemented", success=True,
    )


_EXECUTOR_MAP.update({
    "command": execute_command,
    "prompt": execute_prompt,
    "http": execute_http,
    "agent": execute_agent,
})


async def execute_action(
    action: Action, ctx: HookContext, work_dir: Path
) -> HookResult:
    """按 action.type 分派到对应执行器;未知类型返回诊断。"""
    executor = _EXECUTOR_MAP.get(action.type)
    if executor is None:
        return HookResult(
            output=f"Unknown action type: {action.type}", success=False,
        )
    return await executor(action, ctx, work_dir)
