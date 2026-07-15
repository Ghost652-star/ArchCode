"""Grep 工具:用正则搜索文件内容。"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from archcode.tools.base import SKIP_DIRS, Tool, ToolResult


class Grep(Tool):
    name = "Grep"
    description = "Search file contents using a regex pattern, returning file:line:content matches."
    category = "read"
    is_concurrency_safe = True

    class Params(BaseModel):
        pattern: str = Field(description="Regex pattern to search for")
        path: str = Field(default=".", description="Base directory to search from")
        include: str = Field(default="", description="Glob filter for filenames (e.g. '*.py')")

    params_model = Params

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = work_dir

    async def execute(self, params: Params) -> ToolResult:
        base = self._work_dir / params.path
        if not base.exists():
            return ToolResult(output=f"Error: path not found: {params.path}", is_error=True)
        try:
            regex = re.compile(params.pattern)
        except re.error as e:
            return ToolResult(output=f"Error: invalid regex: {e}", is_error=True)
        glob_pattern = params.include if params.include else "**/*"
        if not glob_pattern.startswith("**/"):
            glob_pattern = "**/" + glob_pattern
        results: list[str] = []
        for file_path in sorted(base.glob(glob_pattern)):
            if not file_path.is_file():
                continue
            if any(part in SKIP_DIRS for part in file_path.parts):
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            for line_num, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    rel = file_path.relative_to(base).as_posix()
                    results.append(f"{rel}:{line_num}:{line}")
        if not results:
            return ToolResult(output="No matches found.")
        return ToolResult(output="\n".join(results))