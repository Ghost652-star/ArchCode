"""ArchCode 权限系统。

5 层判定：
    1. 危险命令黑名单（dangerous.py）
    2. 安全命令白名单（dangerous.py）
    3. 路径沙箱（sandbox.py）
    4. 权限模式矩阵（modes.py）
    5. HITL 人工确认（agent.py → app.py PermissionModal）

用法：
    from archcode.permissions import PermissionChecker, PermissionMode, PathSandbox

    sandbox = PathSandbox(project_root=str(work_dir))
    checker = PermissionChecker(sandbox=sandbox, mode=PermissionMode.DEFAULT)
    decision = checker.check(tool_name, category, arguments)
"""

from archcode.permissions.checker import Decision, PermissionChecker, extract_content
from archcode.permissions.dangerous import DangerousCommandDetector, is_safe_command
from archcode.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from archcode.permissions.sandbox import PathSandbox

__all__ = [
    "Decision",
    "DecisionEffect",
    "DangerousCommandDetector",
    "PathSandbox",
    "PermissionChecker",
    "PermissionMode",
    "extract_content",
    "is_safe_command",
    "mode_decide",
]
