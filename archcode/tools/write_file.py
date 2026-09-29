"""WriteFile 工具:写文件,自动创建父目录。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from archcode.tools.base import Tool, ToolResult, unified_diff_snippet


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
        old_content = ""
        if path.exists():
            try:
                old_content = path.read_text(encoding="utf-8")
            except Exception:
                old_content = ""  # 旧内容读不出(二进制/编码问题)时不阻塞写入
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params.content, encoding="utf-8")
        except Exception as e:
            return ToolResult(output=f"Error writing file: {e}", is_error=True)
        diff = unified_diff_snippet(old_content, params.content)
        output = f"Successfully wrote to {params.file_path}"
        if diff:
            output += f"\n\n{diff}"
        return ToolResult(output=output)