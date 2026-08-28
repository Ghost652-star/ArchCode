"""本地 Markdown 长期记忆：单条文件、双 scope 索引与受控自动提取。"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from archcode.conversation.manager import ConversationManager
from archcode.llm.events import TextDelta
from archcode.paths import application_data_dir, project_data_dir


MemoryScope = Literal["user", "project"]
MemoryType = Literal["user", "feedback", "project", "reference"]

_USER_TYPES = {"user", "feedback"}
_PROJECT_TYPES = {"project", "reference"}
_ALL_TYPES = _USER_TYPES | _PROJECT_TYPES
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_REVISION_RE = re.compile(r"<!--\s*archcode-memory-index:\s*revision=(\d+)\s*-->")
_MAX_ENTRIES = 200
_MAX_CATALOG_BYTES = 25 * 1024
_MAX_DESCRIPTION_CHARS = 240


@dataclass(frozen=True)
class MemoryHeader:
    path: Path
    relative_path: str
    name: str
    description: str
    memory_type: MemoryType


@dataclass(frozen=True)
class MemoryContext:
    content: str
    user_revision: int
    project_revision: int


class MemoryManager:
    """管理两个 memory 根目录；单条文件是事实来源，索引是派生数据。"""

    def __init__(self, work_dir: str | Path, *, app_data_dir: Path | None = None) -> None:
        self._work_dir = Path(work_dir).resolve()
        self._app_data_dir = (app_data_dir or application_data_dir()).resolve()
        self._write_lock = asyncio.Lock()

    @property
    def user_memory_dir(self) -> Path:
        return self._app_data_dir / "memory"

    @property
    def project_memory_dir(self) -> Path:
        return project_data_dir(self._work_dir) / "memory"

    def load_context(self) -> MemoryContext:
        user_text, user_revision = self._rebuild_catalog("user")
        project_text, project_revision = self._rebuild_catalog("project")
        sections = [text for text in (user_text, project_text) if text]
        content = "\n\n".join(sections)
        if content:
            content += (
                "\n\n## Reading indexed memory\n"
                "Use the existing ReadFile tool when the full body is needed.\n"
                "- Project catalog links are relative to the current work directory: "
                "`.archcode/memory/<link-target>`.\n"
                "- User catalog links are relative to this absolute directory: "
                f"`{self.user_memory_dir}`.\n"
            )
        return MemoryContext(content, user_revision, project_revision)

    def resolve_memory_path(self, scope: MemoryScope, relative_path: str) -> Path | None:
        """只解析当前 scope 索引内的单条 Markdown 记忆，供未来 MemoryRead 使用。"""
        root = self._scope_dir(scope)
        candidate = Path(relative_path)
        if candidate.is_absolute() or candidate.suffix.lower() != ".md" or candidate.name == "MEMORY.md":
            return None
        try:
            path = (root / candidate).resolve()
            path.relative_to(root.resolve())
        except ValueError:
            return None
        if not path.is_file():
            return None
        header = self._read_header(path, root)
        if header is None or not self._type_matches_scope(header.memory_type, scope):
            return None
        return path

    async def extract(self, client: Any, conversation: ConversationManager) -> None:
        """后台提取当前已完成任务；LLM 只能返回提案，不能直接操作文件系统。"""
        snapshot = self._task_snapshot(conversation)
        if not snapshot:
            return
        context = self.load_context()
        prompt = self._build_extraction_prompt(context.content, snapshot)
        extract_conversation = ConversationManager()
        extract_conversation.add_user(prompt)
        text = ""
        try:
            async for event in client.stream(
                extract_conversation,
                system="You extract durable ArchCode memories. Return JSON only.",
            ):
                if isinstance(event, TextDelta):
                    text += event.text
        except Exception:
            return
        operations = self._parse_operations(text)
        if operations:
            await self.apply_operations(operations)

    async def apply_operations(self, operations: list[dict[str, Any]]) -> None:
        """验证提案后写入对应 scope；一次批处理结束后才重建索引。"""
        changed_scopes: set[MemoryScope] = set()
        async with self._write_lock:
            for operation in operations:
                scope = operation.get("scope")
                action = operation.get("op")
                if scope not in {"user", "project"} or action not in {"create", "update", "delete"}:
                    continue
                if action == "create":
                    if self._create(scope, operation):
                        changed_scopes.add(scope)
                elif action == "update":
                    if self._update(scope, operation):
                        changed_scopes.add(scope)
                elif self._delete(scope, operation):
                    changed_scopes.add(scope)
            for scope in changed_scopes:
                self._rebuild_catalog(scope)

    def _create(self, scope: MemoryScope, operation: dict[str, Any]) -> bool:
        memory_type = operation.get("type")
        name = self._clean_metadata(operation.get("name"), 120)
        description = self._clean_metadata(
            operation.get("description"), _MAX_DESCRIPTION_CHARS
        )
        body = self._clean_text(operation.get("content"), 8 * 1024)
        if memory_type not in _ALL_TYPES or not self._type_matches_scope(memory_type, scope):
            return False
        if not name or not description or not body:
            return False
        root = self._scope_dir(scope)
        root.mkdir(parents=True, exist_ok=True)
        filename = f"{self._slug(name)}-{uuid.uuid4().hex[:8]}.md"
        self._atomic_write(root / filename, self._render_memory(name, description, memory_type, body))
        return True

    def _update(self, scope: MemoryScope, operation: dict[str, Any]) -> bool:
        relative_path = operation.get("path")
        if not isinstance(relative_path, str):
            return False
        path = self.resolve_memory_path(scope, relative_path)
        if path is None:
            return False
        root = self._scope_dir(scope)
        old = self._read_header(path, root)
        if old is None:
            return False
        memory_type = operation.get("type", old.memory_type)
        name = self._clean_metadata(operation.get("name", old.name), 120)
        description = self._clean_metadata(
            operation.get("description", old.description), _MAX_DESCRIPTION_CHARS
        )
        body = self._clean_text(operation.get("content"), 8 * 1024)
        if memory_type not in _ALL_TYPES or not self._type_matches_scope(memory_type, scope):
            return False
        if not name or not description or not body:
            return False
        self._atomic_write(path, self._render_memory(name, description, memory_type, body))
        return True

    def _delete(self, scope: MemoryScope, operation: dict[str, Any]) -> bool:
        relative_path = operation.get("path")
        if not isinstance(relative_path, str):
            return False
        path = self.resolve_memory_path(scope, relative_path)
        if path is None:
            return False
        path.unlink()
        return True

    def _rebuild_catalog(self, scope: MemoryScope) -> tuple[str, int]:
        root = self._scope_dir(scope)
        root.mkdir(parents=True, exist_ok=True)
        catalog = root / "MEMORY.md"
        old = self._read_text(catalog)
        old_revision = self._read_revision(old)
        entries = self._scan_headers(root, scope)
        body = self._render_catalog_body(scope, entries)
        old_body = self._strip_revision(old)
        if old_body == body:
            return body, old_revision
        revision = old_revision + 1
        complete = f"<!-- archcode-memory-index: revision={revision} -->\n{body}"
        self._atomic_write(catalog, complete)
        return body, revision

    def _scan_headers(self, root: Path, scope: MemoryScope) -> list[MemoryHeader]:
        result: list[MemoryHeader] = []
        try:
            files = sorted(path for path in root.rglob("*.md") if path.name != "MEMORY.md")
        except OSError:
            return result
        for path in files:
            header = self._read_header(path, root)
            if header is not None and self._type_matches_scope(header.memory_type, scope):
                result.append(header)
            if len(result) >= _MAX_ENTRIES:
                break
        return result

    def _read_header(self, path: Path, root: Path) -> MemoryHeader | None:
        content = self._read_text(path)
        match = _FRONTMATTER_RE.match(content)
        if match is None:
            return None
        fields: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"\'')
        memory_type = fields.get("type", "")
        if memory_type not in _ALL_TYPES:
            return None
        name = self._clean_metadata(fields.get("name"), 120)
        description = self._clean_metadata(
            fields.get("description"), _MAX_DESCRIPTION_CHARS
        )
        if not name or not description:
            return None
        try:
            relative = str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
        except ValueError:
            return None
        return MemoryHeader(path, relative, name, description, memory_type)

    @staticmethod
    def _render_memory(name: str, description: str, memory_type: str, body: str) -> str:
        return (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"type: {memory_type}\n"
            "---\n\n"
            f"{body.strip()}\n"
        )

    @staticmethod
    def _render_catalog_body(scope: MemoryScope, entries: list[MemoryHeader]) -> str:
        title = "# User Memory Catalog" if scope == "user" else "# Project Memory Catalog"
        lines = [title, ""]
        used = len(("\n".join(lines) + "\n").encode("utf-8"))
        for header in entries:
            line = f"- [{header.name}]({header.relative_path}) — {header.description}\n"
            size = len(line.encode("utf-8"))
            if used + size > _MAX_CATALOG_BYTES:
                lines.append("- [catalog truncated] — more memory entries exist on disk\n")
                break
            lines.append(line.rstrip("\n"))
            used += size
        return "\n".join(lines).strip() + "\n"

    def _scope_dir(self, scope: MemoryScope) -> Path:
        return self.user_memory_dir if scope == "user" else self.project_memory_dir

    @staticmethod
    def _type_matches_scope(memory_type: str, scope: MemoryScope) -> bool:
        return memory_type in (_USER_TYPES if scope == "user" else _PROJECT_TYPES)

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ""

    @staticmethod
    def _read_revision(content: str) -> int:
        match = _REVISION_RE.search(content)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _strip_revision(content: str) -> str:
        return _REVISION_RE.sub("", content, count=1).lstrip("\n")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp"
        ) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        temporary.replace(path)

    @staticmethod
    def _clean_text(value: object, limit: int) -> str:
        return value.strip()[:limit] if isinstance(value, str) else ""

    @staticmethod
    def _clean_metadata(value: object, limit: int) -> str:
        """frontmatter 单行字段不接受模型输出的换行或控制字符。"""
        if not isinstance(value, str):
            return ""
        return " ".join(value.split())[:limit]

    @staticmethod
    def _slug(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
        slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
        return slug[:48] or "memory"

    @staticmethod
    def _task_snapshot(conversation: ConversationManager) -> str:
        lines: list[str] = []
        for message in conversation.history:
            if "<memory-context>" in message.content or message.role == "system":
                continue
            if message.role == "user" and message.content:
                lines.append(f"User: {message.content}")
            elif message.role == "assistant" and message.content:
                lines.append(f"Assistant: {message.content}")
        return "\n".join(lines[-40:])

    @staticmethod
    def _build_extraction_prompt(catalog: str, snapshot: str) -> str:
        return (
            "Review one completed agent task and propose only durable long-term memories. "
            "Ignore transient work, raw tool output, greetings, guesses, and details with no future value. "
            "Use scopes user/project and types user, feedback, project, reference. "
            "Return JSON only: {\"operations\":[...]}. Each operation is create, update, delete, or noop. "
            "For update/delete, path must be an existing catalog relative path. "
            "Do not create a duplicate when a catalog entry already expresses the same fact.\n\n"
            f"## Current catalogs\n{catalog or '(empty)'}\n\n"
            f"## Completed task\n{snapshot}"
        )

    @staticmethod
    def _parse_operations(text: str) -> list[dict[str, Any]]:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            return []
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return []
        operations = value.get("operations", []) if isinstance(value, dict) else []
        return [item for item in operations if isinstance(item, dict)] if isinstance(operations, list) else []
