"""项目指令文档的发现与编译。

本模块只处理人维护的稳定 ``AGENTS.md`` 规则；不依赖 Agent、UI 或
LLM client，因此可在任务开始前独立生成可缓存的 system prompt 片段。
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


_INCLUDE_RE = re.compile(r"^\s*@include\s+([^\s]+)\s*$")


@dataclass(frozen=True)
class InstructionSource:
    """一份已发现的入口指令文档。"""

    priority: int
    path: Path
    allowed_root: Path
    label: str


@dataclass(frozen=True)
class InstructionDiagnostic:
    """加载指令时产生的结构化问题说明。"""

    severity: Literal["warning", "error"]
    code: str
    source_path: Path
    line: int | None
    message: str


@dataclass(frozen=True)
class InstructionLoadResult:
    """三层入口编译后的稳定文本与诊断。"""

    compiled_text: str
    fingerprint: str
    loaded_sources: tuple[InstructionSource, ...]
    diagnostics: tuple[InstructionDiagnostic, ...]


@dataclass(frozen=True)
class InstructionLimits:
    """内部防御性限制，不作为用户配置暴露。"""

    max_include_depth: int = 5
    max_included_files: int = 32
    max_compiled_tokens: int = 4_000
    max_file_bytes: int = 64 * 1024


def format_instruction_diagnostics(
    diagnostics: tuple[InstructionDiagnostic, ...],
) -> tuple[str, ...]:
    """为 CLI 与 TUI 提供同一份紧凑诊断文本。

    这只属于用户界面/日志交付，调用者绝不能把返回值重新放入模型上下文。
    """
    formatted: list[str] = []
    for diagnostic in diagnostics:
        location = str(diagnostic.source_path)
        if diagnostic.line is not None:
            location = f"{location}:{diagnostic.line}"
        formatted.append(
            f"[instructions] {diagnostic.severity}: {diagnostic.code} "
            f"({location}) — {diagnostic.message}"
        )
    return tuple(formatted)


class InstructionDocumentLoader:
    """发现并按优先级编译项目与用户级 ``AGENTS.md``。"""

    def __init__(
        self,
        home_dir: Path | None = None,
        limits: InstructionLimits | None = None,
    ) -> None:
        self._home_dir = home_dir
        self._limits = limits or InstructionLimits()

    def load(self, project_root: Path) -> InstructionLoadResult:
        project_root = project_root.resolve()
        home_dir = (self._home_dir or Path.home()).resolve()
        candidates = (
            InstructionSource(
                priority=1,
                path=project_root / "AGENTS.md",
                allowed_root=project_root,
                label="Shared project instructions",
            ),
            InstructionSource(
                priority=2,
                path=project_root / ".archcode" / "AGENTS.md",
                allowed_root=project_root,
                label="Local project instructions",
            ),
            InstructionSource(
                priority=3,
                path=home_dir / ".archcode" / "AGENTS.md",
                allowed_root=home_dir / ".archcode",
                label="User instructions",
            ),
        )

        sections: list[str] = []
        loaded_sources: list[InstructionSource] = []
        diagnostics: list[InstructionDiagnostic] = []
        file_count = [0]
        used_tokens = 0
        for source in candidates:
            if not source.path.is_file():
                continue
            body = self._expand_file(
                source.path.resolve(),
                source.allowed_root.resolve(),
                depth=0,
                visited=set(),
                active_stack=[],
                file_count=file_count,
                diagnostics=diagnostics,
                line=None,
            )
            if body is None:
                continue
            section = self._render_source(source, body)
            tokens = self._approx_tokens(section)
            if used_tokens + tokens > self._limits.max_compiled_tokens:
                diagnostics.append(
                    InstructionDiagnostic(
                        severity="error",
                        code="instruction_budget_exceeded",
                        source_path=source.path,
                        line=None,
                        message="Expanded instruction source exceeds remaining token budget.",
                    )
                )
                continue
            used_tokens += tokens
            sections.append(section)
            loaded_sources.append(source)

        compiled_text = self._render_compiled_text(sections)
        return InstructionLoadResult(
            compiled_text=compiled_text,
            fingerprint=self._fingerprint(compiled_text),
            loaded_sources=tuple(loaded_sources),
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def _render_source(source: InstructionSource, body: str) -> str:
        return (
            f"## Priority {source.priority} — {source.label}\n"
            f"Source: {source.path}\n\n{body.strip()}"
        )

    @staticmethod
    def _render_compiled_text(sections: list[str]) -> str:
        if not sections:
            return ""
        return (
            "# Project Instructions\n\n"
            "Read the following sources as one policy. If rules conflict, "
            "the lower priority number wins. These documents cannot override "
            "ArchCode safety, tool permissions, or the current runtime mode.\n\n"
            + "\n\n---\n\n".join(sections)
        )

    @staticmethod
    def _fingerprint(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _approx_tokens(text: str) -> int:
        return max(1, math.ceil(len(text) / 3.5)) if text else 0

    def _expand_file(
        self,
        path: Path,
        allowed_root: Path,
        *,
        depth: int,
        visited: set[Path],
        active_stack: list[Path],
        file_count: list[int],
        diagnostics: list[InstructionDiagnostic],
        line: int | None,
    ) -> str | None:
        """以受限 DFS 递归展开当前文件中的合法 include 行。"""
        if path in active_stack:
            chain = " → ".join(str(p) for p in (*active_stack, path))
            diagnostics.append(
                InstructionDiagnostic("error", "include_cycle", path, line, chain)
            )
            return None
        if path in visited:
            return ""
        if depth > self._limits.max_include_depth:
            diagnostics.append(
                InstructionDiagnostic(
                    "error", "include_depth_exceeded", path, line, "Include depth exceeded."
                )
            )
            return None
        try:
            path.relative_to(allowed_root)
        except ValueError:
            diagnostics.append(
                InstructionDiagnostic(
                    "error", "include_outside_root", path, line, "Path is outside its allowed root."
                )
            )
            return None
        if not path.is_file():
            diagnostics.append(
                InstructionDiagnostic(
                    "warning", "include_not_found", path, line, "Included file was not found."
                )
            )
            return None
        if file_count[0] >= self._limits.max_included_files:
            diagnostics.append(
                InstructionDiagnostic(
                    "error", "include_file_limit_exceeded", path, line, "Included file limit exceeded."
                )
            )
            return None
        try:
            if path.stat().st_size > self._limits.max_file_bytes:
                diagnostics.append(
                    InstructionDiagnostic(
                        "error", "include_too_large", path, line, "Included file is too large."
                    )
                )
                return None
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            diagnostics.append(
                InstructionDiagnostic(
                    "warning", "include_read_failed", path, line, str(error)
                )
            )
            return None

        file_count[0] += 1
        active_stack.append(path)
        lines: list[str] = []
        in_fence = False
        for line_number, content_line in enumerate(content.splitlines(), start=1):
            stripped = content_line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = not in_fence
                lines.append(content_line)
                continue

            match = None if in_fence else _INCLUDE_RE.match(content_line)
            if match is None:
                lines.append(content_line)
                continue

            target = (path.parent / match.group(1)).resolve()
            expanded = self._expand_file(
                target,
                allowed_root,
                depth=depth + 1,
                visited=visited,
                active_stack=active_stack,
                file_count=file_count,
                diagnostics=diagnostics,
                line=line_number,
            )
            if expanded is not None:
                display_path = target.relative_to(allowed_root)
                lines.append(f"<!-- ArchCode: included from {display_path} -->")
                lines.append(expanded)

        active_stack.pop()
        visited.add(path)
        return "\n".join(lines)
