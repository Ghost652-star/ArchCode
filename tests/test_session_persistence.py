from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from archcode.conversation.manager import ConversationManager
from archcode.conversation.models import ToolResultBlock, ToolUseBlock
from archcode.memory.session import SessionManager


def test_session_meta_indexes_list_data_without_jsonl_version(tmp_path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    conversation = ConversationManager()
    session.bind(conversation)

    conversation.add_user("a" * 60)
    conversation.add_assistant("done", completes_user_turn=True)
    session.close()

    meta_path = session.path.with_suffix(".meta")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    first_record = json.loads(session.path.read_text(encoding="utf-8").splitlines()[0])

    assert meta["id"] == session.id
    assert meta["title"] == "a" * 50
    assert meta["message_count"] == 2
    assert meta["last_active_ms"] >= meta["created_at_ms"]
    assert "v" not in first_record
    assert manager.list_sessions()[0].id == session.id


def test_session_reuses_its_append_handle_until_close(tmp_path) -> None:
    session = SessionManager(tmp_path).create()

    assert not session._file.closed

    session.close()
    session.close()

    assert session._file.closed


def test_creating_a_session_prunes_expired_sessions(tmp_path) -> None:
    manager = SessionManager(tmp_path)
    expired = manager.create()
    expired.close()
    expired.meta.last_active_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=31)).timestamp() * 1000
    )
    expired.meta.save(expired.path.with_suffix(".meta"))

    current = manager.create()
    current.close()

    assert not expired.path.exists()
    assert not expired.path.with_suffix(".meta").exists()
    assert current.path.exists()


def test_bound_conversation_round_trips_messages_and_tool_results(tmp_path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    conversation = ConversationManager()
    session.bind(conversation)

    conversation.add_user("read the project settings")
    conversation.add_assistant(
        "I will inspect it.",
        tool_uses=[
            ToolUseBlock(
                tool_use_id="call_1",
                tool_name="ReadFile",
                arguments={"file_path": "settings.py"},
            )
        ],
    )
    conversation.add_tool_results(
        [ToolResultBlock(tool_use_id="call_1", content="DEBUG = false")]
    )
    conversation.add_assistant("The setting is disabled.", completes_user_turn=True)
    session.close()

    restored = manager.open(session.id)

    assert restored is not None
    assert [message.role for message in restored.conversation.history] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert restored.conversation.history[1].tool_uses[0].tool_use_id == "call_1"
    assert restored.conversation.history[2].tool_results[0].content == "DEBUG = false"
    assert restored.conversation.history[-1].completes_user_turn


def test_unmatched_tool_tail_becomes_recovery_material_not_tool_history(tmp_path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    conversation = ConversationManager()
    session.bind(conversation)

    conversation.add_user("inspect both configs")
    conversation.add_assistant(
        "Reading both files.",
        tool_uses=[
            ToolUseBlock("call_1", "ReadFile", {"file_path": "a.py"}),
            ToolUseBlock("call_2", "ReadFile", {"file_path": "b.py"}),
        ],
    )
    conversation.add_tool_results(
        [ToolResultBlock(tool_use_id="call_1", content="a.py content")]
    )
    conversation.add_assistant("The second config looks wrong.", completes_user_turn=True)
    session.close()

    restored = manager.open(session.id)

    assert restored is not None
    assert all(not message.tool_uses for message in restored.conversation.history)
    assert "会话恢复材料" in restored.conversation.history[-2].content
    assert "call_1" in restored.conversation.history[-2].content
    assert "仅可作为线索" in restored.conversation.history[-1].content


def test_restoring_a_session_idle_over_24_hours_adds_time_gap_reminder(tmp_path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    conversation = ConversationManager()
    session.bind(conversation)
    conversation.add_user("continue the work")
    conversation.add_assistant("I will continue.", completes_user_turn=True)
    session.close()
    session.meta.last_active_ms = int(
        (datetime.now(timezone.utc) - timedelta(hours=25)).timestamp() * 1000
    )
    session.meta.save(session.path.with_suffix(".meta"))

    restored = manager.open(session.id)

    assert restored is not None
    assert "距离上次会话已超过 24 小时" in restored.conversation.history[-2].content
    assert "重新读取、检查或向用户确认" in restored.conversation.history[-1].content


def test_latest_checkpoint_replaces_compacted_prefix_on_restore(tmp_path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    conversation = ConversationManager()
    session.bind(conversation)

    conversation.add_user("old context")
    conversation.add_assistant("old response", completes_user_turn=True)
    keep = [
        conversation.history[-1],
    ]
    session.append_checkpoint(summary="compressed history", keep_messages=keep)
    conversation.replace_history(keep)
    conversation.add_user("new question")
    session.close()

    restored = manager.open(session.id)

    assert restored is not None
    contents = [message.content for message in restored.conversation.history]
    assert "compressed history" in contents[0]
    assert "old context" not in contents
    assert contents[-1] == "new question"
