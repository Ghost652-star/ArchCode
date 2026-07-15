"""ReadFile 工具:读文件,返回带行号的内容。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from archcode.tools.base import Tool, ToolResult


class ReadFile(Tool):
    name = "ReadFile"
    description = "Read a file and return its contents with line numbers."
    category = "read"
    is_concurrency_safe = True

    class Params(BaseModel):
        file_path: str = Field(description="Absolute or relative path to the file to read")
        offset: int = Field(default=0, description="Line offset to start reading from (0-based)")
        limit: int = Field(default=2000, description="Maximum number of lines to read")

    params_model = Params

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = work_dir

    async def execute(self, params: Params) -> ToolResult:
        path = self._work_dir / params.file_path
        if not path.exists():
            return ToolResult(output=f"Error: file not found: {params.file_path}", is_error=True)
        if not path.is_file():
            return ToolResult(output=f"Error: not a file: {params.file_path}", is_error=True)
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)
        lines = text.splitlines()
        selected = lines[params.offset : params.offset + params.limit]
        numbered = [f"{i + params.offset + 1}\t{line}" for i, line in enumerate(selected)]
        return ToolResult(output="\n".join(numbered))