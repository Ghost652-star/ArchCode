"""ArchCode 工具系统。

公开 API:
- Tool / ToolResult / ToolCategory / SKIP_DIRS / MAX_OUTPUT_CHARS — 基础类型
- ToolRegistry — 工具注册中心
- create_default_registry(work_dir) — 工厂函数,创建默认 registry

六个基础工具:
- ReadFile / WriteFile / EditFile — 文件操作
- Bash — shell 命令
- Glob / Grep — 搜索
"""

from archcode.tools.base import (
    MAX_OUTPUT_CHARS,
    SKIP_DIRS,
    Tool,
    ToolCategory,
    ToolResult,
)
from archcode.tools.bash import Bash
from archcode.tools.edit_file import EditFile
from archcode.tools.glob import Glob
from archcode.tools.grep import Grep
from archcode.tools.read_file import ReadFile
from archcode.tools.registry import ToolRegistry
from archcode.tools.write_file import WriteFile

__all__ = [
    "MAX_OUTPUT_CHARS",
    "SKIP_DIRS",
    "Bash",
    "EditFile",
    "Glob",
    "Grep",
    "ReadFile",
    "Tool",
    "ToolCategory",
    "ToolRegistry",
    "ToolResult",
    "WriteFile",
    "create_default_registry",
]


def create_default_registry(work_dir) -> ToolRegistry:
    """创建默认工具注册中心,包含 6 个基础工具。

    Args:
        work_dir: 工作目录(传给文件类工具用于路径解析)。
    """
    registry = ToolRegistry()
    registry.register(ReadFile(work_dir=work_dir))
    registry.register(WriteFile(work_dir=work_dir))
    registry.register(EditFile(work_dir=work_dir))
    registry.register(Bash())
    registry.register(Glob(work_dir=work_dir))
    registry.register(Grep(work_dir=work_dir))
    return registry