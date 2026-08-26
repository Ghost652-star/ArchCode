"""项目指令文档的发现与编译。

本模块只处理人维护的稳定 ``AGENTS.md`` 规则；不依赖 Agent、UI 或
LLM client，因此可在任务开始前独立生成可缓存的 system prompt 片段。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


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


class InstructionDocumentLoader:
    """发现并按优先级编译项目与用户级 ``AGENTS.md``。"""

    def __init__(self, home_dir: Path | None = None) -> None:
        self._home_dir = home_dir

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
        for source in candidates:
            if not source.path.is_file():
                continue
            body = source.path.read_text(encoding="utf-8")
            sections.append(self._render_source(source, body))
            loaded_sources.append(source)

        compiled_text = self._render_compiled_text(sections)
        return InstructionLoadResult(
            compiled_text=compiled_text,
            fingerprint=self._fingerprint(compiled_text),
            loaded_sources=tuple(loaded_sources),
            diagnostics=(),
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
