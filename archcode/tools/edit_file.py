"""EditFile 工具:用 old_string → new_string 精确替换(要求 old_string 唯一)。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from archcode.tools.base import Tool, ToolResult


class EditFile(Tool):
    name = "EditFile"
    description = "Replace an exact string in a file. The old_string must appear exactly once in the file."
    category = "write"

    class Params(BaseModel):
        file_path: str = Field(description="Path to the file to edit")
        old_string: str = Field(description="The exact string to find and replace (must be unique in file)")
        new_string: str = Field(description="The replacement string")

    params_model = Params

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = work_dir

    async def execute(self, params: Params) -> ToolResult:
        path = self._work_dir / params.file_path
        if not path.exists():
            return ToolResult(output=f"Error: file not found: {params.file_path}", is_error=True)
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)
        count = content.count(params.old_string)
        if count == 0:
            return ToolResult(output="Error: old_string not found in file", is_error=True)
        if count > 1:
            return ToolResult(
                output=f"Error: old_string found {count} times, must be unique",
                is_error=True,
            )
        new_content = content.replace(params.old_string, params.new_string, 1)
        try:
            path.write_text(new_content, encoding="utf-8")
        except Exception as e:
            return ToolResult(output=f"Error writing file: {e}", is_error=True)
        return ToolResult(output=f"Successfully edited {params.file_path}")