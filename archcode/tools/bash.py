"""Bash 工具:执行 shell 命令,捕获 stdout/stderr,支持超时。"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from archcode.tools.base import Tool, ToolResult

MAX_TIMEOUT = 600


class Bash(Tool):
    name = "Bash"
    description = "Execute a shell command and return stdout and stderr."
    category = "command"

    class Params(BaseModel):
        command: str = Field(description="Shell command to execute")
        timeout: int = Field(default=120, description="Timeout in seconds (max 600)")

    params_model = Params

    async def execute(self, params: Params) -> ToolResult:
        timeout = min(params.timeout, MAX_TIMEOUT)
        try:
            proc = await asyncio.create_subprocess_shell(
                params.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return ToolResult(
                output=f"Error: command timed out after {timeout}s", is_error=True
            )
        except Exception as e:
            return ToolResult(output=f"Error executing command: {e}", is_error=True)

        parts: list[str] = []
        if stdout:
            parts.append(f"STDOUT:\n{stdout.decode(errors='replace')}")
        if stderr:
            parts.append(f"STDERR:\n{stderr.decode(errors='replace')}")
        if not parts:
            parts.append("(no output)")
        return ToolResult(output="\n".join(parts), is_error=proc.returncode != 0)