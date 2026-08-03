"""路径沙箱 —— 权限判定第二层。

确保文件操作（读/写）只发生在允许的目录范围内，
防止 LLM 通过工具调用读取或修改沙箱外的敏感文件。
"""

from __future__ import annotations

import tempfile
from pathlib import Path


class PathSandbox:
    """路径白名单校验器。

    所有文件类工具（ReadFile / WriteFile / EditFile / Glob / Grep）
    操作的目标路径必须落在 allowed_roots 内，否则 deny。

    默认白名单：
    - work_dir（项目根目录）
    - 系统临时目录（tempfile.gettempdir()）
    - 用户通过 extra_allowed 添加的额外路径
    """

    def __init__(
        self,
        project_root: str,
        extra_allowed: list[str] | None = None,
    ) -> None:
        root = Path(project_root).resolve()
        self._allowed_roots: list[Path] = [
            root,
            Path(tempfile.gettempdir()).resolve(),
        ]
        if extra_allowed:
            for p in extra_allowed:
                self._allowed_roots.append(Path(p).resolve())

    @property
    def project_root(self) -> Path:
        return self._allowed_roots[0]

    def check(self, path: str) -> tuple[bool, str]:
        """检查 path 是否在沙箱范围内。

        返回 (ok, reason)。ok=True 表示放行，ok=False 时 reason 携带拦截原因。
        """
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.project_root / p
        abs_path = p.absolute()

        try:
            real_path = abs_path.resolve(strict=True)
        except OSError:
            # 路径不存在 → 找到最近的已存在祖先，解析后再拼接剩余部分
            ancestor = abs_path
            while not ancestor.exists():
                parent = ancestor.parent
                if parent == ancestor:
                    return False, f"无法解析路径: {path}"
                ancestor = parent
            try:
                resolved_ancestor = ancestor.resolve(strict=True)
            except OSError:
                return False, f"无法解析路径: {path}"
            real_path = resolved_ancestor / abs_path.relative_to(ancestor)

        for root in self._allowed_roots:
            try:
                real_path.relative_to(root)
                return True, ""
            except ValueError:
                continue

        return False, f"路径 {path} 超出沙箱范围"
