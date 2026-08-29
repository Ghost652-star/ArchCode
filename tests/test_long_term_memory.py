from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from archcode.agent import Agent
from archcode.conversation.manager import ConversationManager
from archcode.llm.client import LLMClient
from archcode.llm.events import StreamEnd, StreamEvent, TextDelta
from archcode.memory.long_term import MemoryManager


def _write_memory(path: Path, *, name: str, description: str, memory_type: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"type: {memory_type}\n"
        "---\n\n"
        "记忆正文。\n",
        encoding="utf-8",
    )


def test_memory_manager_builds_scoped_catalogs_and_revisions(tmp_path: Path) -> None:
    app_data = tmp_path / "application" / ".archcode"
    project = tmp_path / "project"
    _write_memory(
        app_data / "memory" / "reply-chinese.md",
        name="中文回复",
        description="用户偏好使用中文回复",
        memory_type="user",
    )
    _write_memory(
        project / ".archcode" / "memory" / "ci.md",
        name="CI 方式",
        description="项目使用 GitHub Actions",
        memory_type="project",
    )

    manager = MemoryManager(project, app_data_dir=app_data)
    context = manager.load_context()

    assert "中文回复" in context.content
    assert "CI 方式" in context.content
    assert ".archcode/memory/<link-target>" in context.content
    assert str(app_data / "memory") in context.content
    assert context.user_revision == 1
    assert context.project_revision == 1
    assert (app_data / "memory" / "MEMORY.md").is_file()
    assert (project / ".archcode" / "memory" / "MEMORY.md").is_file()


def test_conversation_replaces_memory_context_only_when_versions_change() -> None:
    conversation = ConversationManager()

    assert conversation.refresh_memory_context("catalog v1", 1, 1) is True
    assert conversation.refresh_memory_context("catalog v1", 1, 1) is False
    assert len(conversation.history) == 1
    assert "catalog v1" in conversation.history[0].content

    conversation.add_user("真实用户消息")
    assert conversation.refresh_memory_context("catalog v2", 2, 1) is True

    memory_messages = [m for m in conversation.history if "<memory-context>" in m.content]
    assert len(memory_messages) == 1
    assert "catalog v2" in memory_messages[0].content
    assert conversation.history[-1].content == "真实用户消息"


def test_memory_context_is_restored_after_history_replacement(tmp_path: Path) -> None:
    manager = MemoryManager(tmp_path / "project", app_data_dir=tmp_path / "app")
    conversation = ConversationManager()
    context = manager.load_context()

    conversation.refresh_memory_context(
        "catalog", context.user_revision, context.project_revision
    )
    conversation.replace_history([])

    assert conversation.history == []
    assert conversation.refresh_memory_context(
        "catalog", context.user_revision, context.project_revision
    ) is True
    assert len(conversation.history) == 1


@pytest.mark.asyncio
async def test_memory_manager_ignores_placeholder_operations(tmp_path: Path) -> None:
    project = tmp_path / "project"
    manager = MemoryManager(project, app_data_dir=tmp_path / "app")

    await manager.apply_operations(
        [
            {
                "op": "create",
                "scope": "project",
                "type": "project",
                "name": "N/A",
                "description": "暂无",
                "content": "...",
            },
            {
                "op": "create",
                "scope": "project",
                "type": "project",
                "name": "部署现状",
                "description": "当前没有部署脚本，需要后续补充",
                "content": "暂无部署脚本，后续需要补充。",
            },
        ]
    )

    memories = [
        path
        for path in manager.project_memory_dir.glob("*.md")
        if path.name != "MEMORY.md"
    ]

    assert len(memories) == 1
    assert "暂无部署脚本，后续需要补充" in memories[0].read_text(encoding="utf-8")


class _FinalReplyClient(LLMClient):
    protocol = "anthropic"

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        yield TextDelta("完成")
        yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=2)


@pytest.mark.asyncio
async def test_agent_injects_catalog_before_the_new_user_message(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_memory(
        project / ".archcode" / "memory" / "project-rule.md",
        name="项目规则",
        description="使用项目约定的格式化命令",
        memory_type="project",
    )
    conversation = ConversationManager()
    agent = Agent(_FinalReplyClient(), "base system", work_dir=project)

    async for _ in agent.run("处理任务", conversation):
        pass

    assert "<memory-context>" in conversation.history[0].content
    assert "项目规则" in conversation.history[0].content
    assert conversation.history[1].content == "处理任务"
