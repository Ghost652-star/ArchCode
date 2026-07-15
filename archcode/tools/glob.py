"""Glob 工具:文件名 glob 匹配。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from archcode.tools.base import SKIP_DIRS, Tool, ToolResult


class Glob(Tool):
    name = "Glob"
    description = "Find files matching a glob pattern, returning relative paths."
    category = "read"
    is_concurrency_safe = True

    class Params(BaseModel):
        pattern: str = Field(description="Glob pattern to match (e.g. '**/*.py')")
        path: str = Field(default=".", description="Base directory to search from")

    params_model = Params

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = work_dir

    async def execute(self, params: Params) -> ToolResult:
        base = self._work_dir / params.path
        if not base.exists():
            return ToolResult(output=f"Error: path not found: {params.path}", is_error=True)
        try:
            matches = sorted(
                p.relative_to(base).as_posix()
                for p in base.glob(params.pattern)
                if p.is_file() and not any(part in SKIP_DIRS for part in p.parts)
            )
        except Exception as e:
            return ToolResult(output=f"Error: {e}", is_error=True)
        if not matches:
            return ToolResult(output="No files matched the pattern.")
        return ToolResult(output="\n".join(matches))