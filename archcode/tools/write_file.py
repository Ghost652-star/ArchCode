"""WriteFile 工具:写文件,自动创建父目录。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from archcode.tools.base import Tool, ToolResult


class WriteFile(Tool):
    name = "WriteFile"
    description = "Write content to a file, creating parent directories if needed. Overwrites existing files."
    category = "write"

    class Params(BaseModel):
        file_path: str = Field(description="Path to the file to write")
        content: str = Field(description="Content to write to the file")

    params_model = Params

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = work_dir

    async def execute(self, params: Params) -> ToolResult:
        path = self._work_dir / params.file_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params.content, encoding="utf-8")
        except Exception as e:
            return ToolResult(output=f"Error writing file: {e}", is_error=True)
        return ToolResult(output=f"Successfully wrote to {params.file_path}")