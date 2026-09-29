"""ArchCode Web 服务(FastAPI):把 AgentEvent 流桥接成 SSE,供 web/ 前端消费。

装配与 TUI 完全同源(__main__ 的 _build_agent_sync + _wire_hooks + _wire_skills),
agent 零改动——Web 只是 AgentEvent 流的另一个客户端(hooks-design/webui §8.5 Q5)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from archcode.agent import (
    Agent,
    CompactFinished,
    CompactProgress,
    CompactStarted,
    ErrorEvent,
    InstructionDiagnosticsEvent,
    LoopComplete,
    PermissionRequest,
    RetryEvent,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)
from archcode.conversation.manager import ConversationManager
from archcode.logctx import set_session_id
from archcode.memory import SessionManager

log = logging.getLogger(__name__)

# ── 全局服务状态(单进程单 agent,学习项目) ─────────────────────────────


class ServerState:
    """服务端持有的运行时:agent、会话、HITL 注册表、任务锁、用量累计。"""

    def __init__(self, agent: Agent, work_dir: Path, providers: list | None = None) -> None:
        self.agent = agent
        self.work_dir = work_dir
        self.providers = providers or []  # ProviderConfig 列表(模型选择器用)
        self.session_manager = SessionManager(work_dir)
        self.conversation = ConversationManager()
        self._session = self.session_manager.create()
        self._session.bind(self.conversation)
        log.info("session created: %s", self._session.id)
        set_session_id(self._session.id)
        self._run_lock = asyncio.Lock()
        self._permissions: dict[str, asyncio.Future] = {}
        self._pending_permits: list[dict] = []  # 重连时重发的未决请求
        self._usage = self._usage_from_meta()

    # ── 用量累计(数据源:agent 每轮 yield 的 UsageEvent;持久层:.meta 索引)──

    def _usage_from_meta(self) -> dict:
        meta = self._session.meta if self._session else None
        return {
            "input": int(getattr(meta, "input_tokens", 0) or 0),
            "output": int(getattr(meta, "output_tokens", 0) or 0),
            "cache_read": int(getattr(meta, "cache_read_tokens", 0) or 0),
            "cache_creation": int(getattr(meta, "cache_creation_tokens", 0) or 0),
            "rounds": int(getattr(meta, "llm_rounds", 0) or 0),
        }

    def record_usage(self, event: UsageEvent, turn: dict) -> None:
        """本轮 LLM 用量:进单轮小计 + 会话累计(落 .meta)。"""
        fields = (
            ("input", "input_tokens", event.input_tokens),
            ("output", "output_tokens", event.output_tokens),
            ("cache_read", "cache_read", event.cache_read),
            ("cache_creation", "cache_creation", event.cache_creation),
        )
        for turn_key, _, value in fields:
            turn[turn_key] += int(value)
        self._usage["rounds"] += 1
        turn["rounds"] += 1
        if self._session is not None:
            self._session.accumulate_usage(
                event.input_tokens, event.output_tokens,
                event.cache_read, event.cache_creation,
            )

    # ── 会话 ──

    def new_session(self) -> str:
        self.agent.clear_active_skills()
        old = self._session
        self.conversation = ConversationManager()
        self._session = self.session_manager.create()
        self._session.bind(self.conversation)
        if old is not None:
            old.close()
        log.info("session rotated: %s -> %s", old.id if old else "-", self._session.id)
        set_session_id(self._session.id)
        self._usage = self._usage_from_meta()
        store = _todo_store()
        if store is not None:
            store.clear()  # 任务清单是会话作用域,会话轮转即清空
        return self._session.id

    def resume_session(self, session_id: str) -> None:
        restored = self.session_manager.open(session_id)
        if restored is None:
            raise HTTPException(404, f"session not found: {session_id}")
        self.agent.clear_active_skills()
        old = self._session
        self._session = restored.session
        self.conversation = restored.conversation
        if old is not None:
            old.close()
        log.info("session resumed: %s", session_id)
        set_session_id(session_id)
        self._usage = self._usage_from_meta()
        store = _todo_store()
        if store is not None:
            store.clear()

    def list_sessions(self) -> list[dict]:
        running = self._run_lock.locked()
        current_id = self._session.id if self._session else None
        sessions = self.session_manager.list_sessions()
        return [
            _session_row(
                s,
                current=s.id == current_id,
                running=running and s.id == current_id,
            )
            for s in sessions
        ]

    # ── HITL 桥(§8.5 Q3)──

    def register_permission(self, req: PermissionRequest) -> str:
        rid = uuid.uuid4().hex
        self._permissions[rid] = req.future
        return rid

    def resolve_permission(self, request_id: str, value) -> None:
        future = self._permissions.pop(request_id, None)
        if future is None or future.done():
            raise HTTPException(404, f"unknown or resolved permission: {request_id}")
        future.set_result(value)


STATE: ServerState | None = None

app = FastAPI(title="ArchCode Web")


# ── 事件序列化(AgentEvent → wire JSON,plan §4.2)────────────────────────


_TYPE_MAP = {
    StreamText: ("text", lambda e: {"text": e.text}),
    ThinkingText: ("thinking", lambda e: {"text": e.text}),
    TurnComplete: ("turn_complete", lambda e: {"turn": e.turn}),
    ErrorEvent: ("error", lambda e: {"message": e.message}),
    LoopComplete: (
        "loop_complete",
        lambda e: {"total_turns": e.total_turns, "text": e.text},
    ),
    UsageEvent: (
        "usage",
        lambda e: {
            "input_tokens": e.input_tokens,
            "output_tokens": e.output_tokens,
            "cache_read": e.cache_read,
            "cache_creation": e.cache_creation,
        },
    ),
    RetryEvent: ("retry", lambda e: {"reason": e.reason, "wait": e.wait}),
    CompactStarted: ("compact_started", lambda e: {"mode": e.mode}),
    CompactProgress: (
        "compact_progress",
        lambda e: {"delta": e.delta, "total_chars": e.total_chars},
    ),
    CompactFinished: (
        "compact_finished",
        lambda e: {
            "success": e.success,
            "error": e.error,
            "dropped": e.dropped,
            "summary_preview": e.summary_preview,
        },
    ),
}


def serialize_event(event) -> dict:
    """AgentEvent → wire JSON dict(plan §4.2 映射表)。"""
    if isinstance(event, PermissionRequest):
        assert STATE is not None
        rid = STATE.register_permission(event)
        return {
            "type": "permission_request",
            "request_id": rid,
            "tool_name": event.tool_name,
            "category": event.category,
            "reason": event.reason,
            "question": event.question,
            "options": event.options,
            "multi_select": event.multi_select,
        }
    if isinstance(event, ToolUseEvent):
        return {
            "type": "tool_use",
            "tool_id": event.tool_id,
            "tool_name": event.tool_name,
            "arguments": event.arguments,
        }
    if isinstance(event, ToolResultEvent):
        payload = {
            "type": "tool_result",
            "tool_id": event.tool_id,
            "tool_name": event.tool_name,
            "output": event.output,
            "is_error": event.is_error,
            "elapsed": event.elapsed,
        }
        if event.tool_name == "TodoWrite":
            payload["todos"] = _todo_snapshot()
        return payload
    if isinstance(event, InstructionDiagnosticsEvent):
        return {
            "type": "instruction_diagnostics",
            "diagnostics": [asdict(d) for d in event.diagnostics],
        }
    mapping = _TYPE_MAP.get(type(event))
    if mapping is None:
        return {"type": "unknown", "repr": repr(event)}
    wire_type, fields = mapping
    payload = {"type": wire_type, **fields(event)}
    payload["ts"] = int(time.time() * 1000)  # 事件发出时刻(§13-A1)
    return payload


def _sse(payload: dict) -> str:
    return f"event: agent\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _todo_store():
    """TodoWrite 工具实例上的会话级清单 store(未注册/被禁用时为 None)。"""
    assert STATE is not None
    registry = getattr(STATE.agent, "_tool_registry", None)
    tool = registry.get("TodoWrite") if registry is not None else None
    return getattr(tool, "store", None)


def _todo_snapshot() -> list[dict]:
    store = _todo_store()
    return list(store.todos) if store is not None else []


# ── 端点 ─────────────────────────────────────────────────────────────────


@app.get("/api/state")
def api_state():
    assert STATE is not None
    checker = getattr(STATE.agent, "_permission_checker", None)
    provider = getattr(STATE.agent, "_client", None)
    return {
        "session_id": STATE._session.id if STATE._session else None,
        "running": STATE._run_lock.locked(),
        "work_dir": str(STATE.work_dir),
        "model": getattr(provider, "model_name", ""),
        "permission_mode": checker.mode.value if checker else "default",
        "plan_mode": bool(getattr(STATE.agent, "_plan_mode", False)),
    }


@app.get("/api/sessions")
def api_sessions(workspace: str | None = None):
    """会话清单;`?workspace=<path>` 可列出其他工作区的会话(只读,不切换 agent)。"""
    assert STATE is not None
    if workspace:
        from archcode.memory import SessionManager as _SM

        ws = Path(workspace)
        if not ws.exists() or ws.resolve() == STATE.work_dir.resolve():
            return STATE.list_sessions()
        other = _SM(ws)
        return [_session_row(s, workspace=str(ws)) for s in other.list_sessions()]
    return STATE.list_sessions()


def _session_row(s, **extra) -> dict:
    """SessionMeta → wire 行(设计 §10.9-4:标题 + 相对时间数据源)。"""
    return {
        "id": s.id,
        "title": s.title,
        "message_count": s.message_count,
        "last_active_ms": s.last_active_ms,
        "created": str(getattr(s, "created_at", "")),
        "current": bool(extra.pop("current", False)),
        "running": bool(extra.pop("running", False)),
        **extra,
    }


@app.post("/api/sessions/{session_id}/rename")
def api_rename_session(session_id: str, body: dict):
    assert STATE is not None
    title = str((body or {}).get("title", "")).strip()
    if not title:
        raise HTTPException(400, "title is required")
    if not STATE.session_manager.rename(session_id, title):
        raise HTTPException(404, f"session not found: {session_id}")
    if STATE._session is not None and STATE._session.id == session_id:
        STATE._session.meta.title = title  # 同步内存态,防 _touch_meta 把旧名写回
    return {"ok": True}


@app.delete("/api/sessions/{session_id}")
def api_delete_session(session_id: str):
    assert STATE is not None
    is_current = STATE._session is not None and STATE._session.id == session_id
    if is_current and STATE._run_lock.locked():
        raise HTTPException(409, "cannot delete the running session")
    if is_current:
        STATE._session.close()  # 先关句柄,Windows 下不关无法删除
    if not STATE.session_manager.delete(session_id):
        raise HTTPException(404, f"session not found: {session_id}")
    if is_current:
        STATE.new_session()  # 删的是当前会话:自动开新会话,页面始终有落点
    return {"ok": True}


# ── 模型选择器(§9.1 ModelsSection 模式:当前 + 已配置清单)──────────────


@app.get("/api/model")
def api_model():
    assert STATE is not None
    client = getattr(STATE.agent, "_client", None)
    return {
        "current": getattr(client, "model_name", ""),
        "providers": [
            {"name": p.name, "model": p.model, "protocol": p.protocol}
            for p in STATE.providers
        ],
    }


@app.post("/api/model")
def api_model_switch(body: dict):
    """切换模型:按名称重建 agent._client(运行中拒绝)。"""
    assert STATE is not None
    if STATE._run_lock.locked():
        raise HTTPException(409, "cannot switch model while a run is active")
    name = (body or {}).get("name", "")
    provider = next((p for p in STATE.providers if p.name == name), None)
    if provider is None:
        raise HTTPException(404, f"provider not found: {name}")
    from archcode.llm.client import create_client

    STATE.agent._client = create_client(provider)
    STATE.agent._client.set_max_output_tokens(provider.max_output_tokens)
    return {"ok": True, "model": provider.model}


@app.post("/api/sessions")
def api_new_session():
    assert STATE is not None
    return {"session_id": STATE.new_session()}


@app.post("/api/sessions/{session_id}/resume")
def api_resume_session(session_id: str):
    assert STATE is not None
    STATE.resume_session(session_id)
    return {"session_id": session_id}


@app.get("/api/sessions/search")
def api_sessions_search(q: str, limit: int = 8):
    """跨会话内容搜索:扫描 .jsonl 正文(每文件前 2MB),返回首次命中摘要。

    只读;搜索范围 = 当前工作区的会话目录,超大文件截断,不求全文精确计数。
    """
    assert STATE is not None
    needle = q.strip().lower()
    if len(needle) < 2:
        return {"results": []}
    results: list[dict] = []
    for meta in STATE.session_manager.list_sessions():
        path = STATE.session_manager.sessions_dir / f"{meta.id}.jsonl"
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        idx = text.lower().find(needle)
        if idx < 0:
            continue
        start = max(0, idx - 60)
        end = min(len(text), idx + len(needle) + 80)
        excerpt = text[start:end].replace("\n", " ").strip()
        results.append({"id": meta.id, "title": meta.title, "excerpt": excerpt})
        if len(results) >= max(1, min(limit, 20)):
            break
    return {"results": results}


@app.get("/api/history")
def api_history():
    """当前会话全量历史(刷新恢复用):按消息角色返回。

    内部注入的 user 消息不进 UI(§10.9-3):`<system-reminder>`(运行时提醒)、
    `<会话恢复材料>`(恢复降级线索)、`[恢复提示]`(时间间隔提示)。
    会话数据不动,只在显示层过滤;对应的 assistant 边界说明是真实回复,保留。
    """
    assert STATE is not None
    internal_prefixes = ("<system-reminder>", "<会话恢复材料>", "[恢复提示]")
    out = []
    for m in STATE.conversation.history:
        if m.role == "user" and m.content.startswith(internal_prefixes):
            continue
        entry: dict = {"role": m.role, "content": m.content, "created_at": m.created_at}
        if m.tool_uses:
            entry["tool_uses"] = [asdict(u) for u in m.tool_uses]
        if m.tool_results:
            entry["tool_results"] = [asdict(r) for r in m.tool_results]
        out.append(entry)
    return out


@app.post("/api/chat")
async def api_chat(body: dict):
    assert STATE is not None
    text = (body or {}).get("text", "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    if STATE._run_lock.locked():
        raise HTTPException(409, "another run is active")

    STATE.agent._abort_event.clear()  # 新一轮重置中断(同 app.py:806)

    async def stream():
        # Web 端斜杠命令:目前只接 /plan(直接调 agent 现成方法),其余提示不支持
        if text.startswith("/"):
            async with STATE._run_lock:
                if text == "/plan":
                    on = not getattr(STATE.agent, "_plan_mode", False)
                    STATE.agent.set_plan_mode(on)
                    yield _sse({
                        "type": "notice",
                        "text": f"Plan 模式已{'开启' if on else '关闭'}",
                    })
                else:
                    yield _sse({
                        "type": "notice",
                        "text": f"Web 端暂不支持斜杠命令 {text.split()[0]}(可在 TUI 使用)",
                    })
                yield _sse({"type": "done"})
            return
        async with STATE._run_lock:
            turn_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "rounds": 0}
            try:
                async for event in STATE.agent.run(text, STATE.conversation):
                    if isinstance(event, UsageEvent):
                        STATE.record_usage(event, turn_usage)
                    yield _sse(serialize_event(event))
            except Exception as e:  # 兜底:agent 内部抛错也走 SSE error
                yield _sse({"type": "error", "message": str(e)})
            finally:
                if turn_usage["rounds"] > 0:
                    total = sum(turn_usage[k] for k in ("input", "output", "cache_read", "cache_creation"))
                    yield _sse({"type": "turn_usage", **turn_usage, "total": total})
                yield _sse({"type": "done"})
                pending = list(STATE._permissions.items())
                STATE._pending_permits = [
                    {"request_id": rid} for rid, _ in pending
                ]

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@app.post("/api/abort")
async def api_abort():
    assert STATE is not None
    log.info("abort requested (web)")
    STATE.agent._abort_event.set()
    return {"ok": True}


@app.post("/api/permission/{request_id}")
async def api_permission(request_id: str, body: dict):
    assert STATE is not None
    allowed = bool((body or {}).get("allowed", False))
    answer = (body or {}).get("answer")
    value = answer if answer is not None else allowed
    STATE.resolve_permission(request_id, value)
    return {"ok": True}


@app.get("/api/context")
def api_context():
    """上下文占用(§13-A4):percent = 当前 token / 窗口(provider 可配,缺省 128k)。

    breakdown 为启发式分段估算,供 composer 旁的占用卡做分类着色:
    系统提示词 / 内建工具 schema / MCP(延迟)工具 schema / 激活 Skill 钉住段 /
    记忆索引段 / 消息(= 总量减去其余,保证闭合)。数字是估算值。
    """
    assert STATE is not None
    total = STATE.conversation.current_tokens()
    provider = STATE.providers[0] if STATE.providers else None
    window = int(getattr(provider, "context_window", 0) or 0) or 131072
    percent = min(1.0, total / window) if window > 0 else 0.0

    def _est(text: str | None) -> int:
        return int(len(text or "") / 3.5)

    system = _est(getattr(STATE.agent, "_system_prompt", ""))
    tools_builtin = tools_mcp = 0
    registry = getattr(STATE.agent, "_tool_registry", None)
    if registry is not None:
        for tool in getattr(registry, "_tools", {}).values():
            try:
                est = _est(json.dumps(tool.get_schema(), ensure_ascii=False))
            except Exception:
                continue
            if getattr(tool, "should_defer", False):
                tools_mcp += est
            else:
                tools_builtin += est
    conversation = STATE.conversation
    skills = _est(getattr(conversation, "_active_skills_message", None) and
                  conversation._active_skills_message.content)
    memory = _est(getattr(conversation, "_memory_context_message", None) and
                  conversation._memory_context_message.content)
    fixed = system + tools_builtin + tools_mcp + skills + memory
    messages = max(total - fixed, 0)
    return {
        "total_tokens": total,
        "percent": percent,
        "window": window,
        "breakdown": {
            "messages": messages,
            "system": system,
            "tools_builtin": tools_builtin,
            "tools_mcp": tools_mcp,
            "skills": skills,
            "memory": memory,
        },
    }


@app.get("/api/usage")
def api_usage():
    """会话累计 LLM 用量(.meta 索引持久化,resume 后可恢复)。"""
    assert STATE is not None
    u = STATE._usage
    billed_input = u["input"] + u["cache_read"] + u["cache_creation"]
    return {
        "input_tokens": u["input"],
        "output_tokens": u["output"],
        "cache_read": u["cache_read"],
        "cache_creation": u["cache_creation"],
        "llm_rounds": u["rounds"],
        "total_tokens": billed_input + u["output"],
        "cache_hit": (u["cache_read"] / billed_input) if billed_input > 0 else None,
    }


# ── 设置(§9.2:作用域选择器;ruamel round-trip 保注释)───────────────────

from ruamel.yaml import YAML  # noqa: E402

_yaml = YAML()
_yaml.preserve_quotes = True


def _scope_path(scope: str, work_dir: Path) -> Path:
    from archcode.paths import application_data_dir, project_data_dir

    if scope == "user":
        return application_data_dir() / "config.yaml"
    if scope == "project":
        return project_data_dir(work_dir) / "config.yaml"
    if scope == "local":
        return project_data_dir(work_dir) / "config.local.yaml"
    raise HTTPException(400, f"unknown scope: {scope}")


@app.get("/api/settings/{scope}")
def api_settings_get(scope: str):
    assert STATE is not None
    path = _scope_path(scope, STATE.work_dir)
    if not path.exists():
        return {"path": str(path), "data": {}, "exists": False}
    data = _yaml.load(path.read_text(encoding="utf-8"))
    return {"path": str(path), "data": data if data is not None else {}, "exists": True}


@app.put("/api/settings/{scope}")
def api_settings_put(scope: str, body: dict):
    assert STATE is not None
    path = _scope_path(scope, STATE.work_dir)
    key = (body or {}).get("key")
    items = (body or {}).get("items")
    if key not in ("providers", "mcp_servers", "hooks") or not isinstance(items, list):
        raise HTTPException(400, "body must be {key, items}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(".yaml.bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        data = _yaml.load(path.read_text(encoding="utf-8")) or {}
    else:
        data = {}
    data[key] = items
    import io

    buf = io.StringIO()
    _yaml.dump(data, buf)
    path.write_text(buf.getvalue(), encoding="utf-8")
    restart_required = key in ("providers", "mcp_servers", "hooks")
    return {"ok": True, "restart_required": restart_required}


@app.post("/api/permission-mode")
def api_permission_mode(body: dict):
    assert STATE is not None
    from archcode.permissions import PermissionMode

    mode = (body or {}).get("mode", "")
    if mode not in ("default", "accept", "bypass"):
        raise HTTPException(400, f"unknown mode: {mode}")
    checker = getattr(STATE.agent, "_permission_checker", None)
    if checker is not None:
        checker.mode = PermissionMode(mode)
    return {"ok": True, "mode": mode}


@app.get("/api/todo")
def api_todo():
    """当前会话任务清单(TodoWrite store 快照)。"""
    return {"todos": _todo_snapshot()}


@app.get("/api/tasks")
def api_tasks():
    """后台任务清单(TaskManager 只读快照):状态/耗时/token 用量/结果预览。"""
    assert STATE is not None
    manager = getattr(STATE.agent, "_task_manager", None)
    if manager is None:
        return {"tasks": []}
    tasks = []
    for bg in manager.list_tasks():
        end = bg.end_time if bg.end_time is not None else time.monotonic()
        tasks.append(
            {
                "id": bg.id,
                "name": bg.name,
                "status": bg.status,
                "elapsed": max(end - bg.start_time, 0.0),
                "input_tokens": bg.progress.input_tokens,
                "output_tokens": bg.progress.output_tokens,
                "result_preview": (bg.result or "")[:200],
            }
        )
    return {"tasks": tasks}


@app.get("/api/agents")
def api_agents():
    """子 agent 定义清单(AgentLoader 三层合并后的生效集合:project > user > builtin)。"""
    assert STATE is not None
    loader = getattr(STATE.agent, "_agent_loader", None)
    if loader is None:
        return []
    return [
        {
            "agent_type": d.agent_type,
            "when_to_use": d.when_to_use,
            "source": d.source,
            "path": str(d.file_path) if d.file_path else "",
            "model": d.model,
            "max_turns": d.max_turns,
            "permission_mode": d.permission_mode,
            "background": d.background,
            "tools": d.tools,
            "disallowed_tools": d.disallowed_tools,
        }
        for d in loader.manifests().values()
    ]


@app.get("/api/skills")
def api_skills():
    assert STATE is not None
    loader = getattr(STATE.agent, "_skill_loader", None)
    if loader is None:
        return []
    return [
        {
            "name": m.name,
            "description": m.description,
            "source": m.source,
            "path": str(m.path),
            "is_directory": m.is_directory,
        }
        for m in loader.manifests().values()
    ]


# ── 工作区文件(只读浏览:设计 §12,路径圈定在工作区内是安全底线)──────────

_MAX_ENTRIES = 2000
_MAX_LINES = 2000
_MAX_BYTES = 512 * 1024


def _confine(rel: str) -> Path:
    """相对路径 → work_dir 内的绝对路径;越界 403、不存在 404。空串 = 根。"""
    assert STATE is not None
    rel = (rel or "").replace("\\", "/").strip("/")
    root = STATE.work_dir.resolve()
    if not rel or rel == ".":
        return root
    if PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
        raise HTTPException(403, "path escapes the workspace")
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(403, "path escapes the workspace")
    if not target.exists():
        raise HTTPException(404, f"not found: {rel}")
    return target


def _natural_key(name: str) -> list:
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


@app.get("/api/files")
def api_files(path: str = ""):
    """列目录直接子项:目录在前、自然序、2000 条上限(设计 §12.2)。"""
    target = _confine(path)
    if not target.is_dir():
        raise HTTPException(400, f"not a directory: {path}")
    entries: list[dict] = []
    for child in target.iterdir():
        try:
            if child.is_dir():
                entries.append({"name": child.name, "type": "directory"})
            elif child.is_file():
                entries.append(
                    {"name": child.name, "type": "file", "size": child.stat().st_size}
                )
            else:
                entries.append({"name": child.name, "type": "other"})
        except OSError:
            entries.append({"name": child.name, "type": "other"})
    entries.sort(key=lambda e: (e["type"] != "directory", _natural_key(e["name"])))
    return {
        "path": path,
        "entries": entries[:_MAX_ENTRIES],
        "truncated": len(entries) > _MAX_ENTRIES,
    }


@app.get("/api/file")
def api_file(path: str):
    """读文本文件:二进制探测 → utf-8/gbk 解码 → 2000 行 / 512KB 截断(设计 §12.4)。"""
    target = _confine(path)
    if not target.is_file():
        raise HTTPException(400, f"not a regular file: {path}")
    size = target.stat().st_size
    with target.open("rb") as f:
        head = f.read(_MAX_BYTES)
    if b"\0" in head[:8192]:
        return {"text": "", "truncated": False, "size": size, "binary": True}
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        text = head.decode("gbk", errors="replace")
    lines = text.split("\n")
    truncated = len(lines) > _MAX_LINES
    return {
        "text": "\n".join(lines[:_MAX_LINES]),
        "truncated": truncated,
        "size": size,
        "binary": False,
    }


# 文件名搜索时跳过的目录(§13-B1:依赖与构建产物无引用价值)
_SEARCH_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".archcode",
    ".pytest_cache", ".idea", ".cursor", ".codegraph", "dist", "build",
}
_SEARCH_SCAN_CAP = 20000


@app.get("/api/files/search")
def api_files_search(q: str = "", limit: int = 20):
    """按文件名子串递归搜索工作区(@ 引用的数据源,§13-B1)。"""
    assert STATE is not None
    needle = q.strip().lower()
    if not needle:
        return {"results": []}
    root = STATE.work_dir.resolve()
    results: list[str] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SEARCH_SKIP_DIRS]
        for name in filenames:
            scanned += 1
            if scanned > _SEARCH_SCAN_CAP:
                return {"results": results}
            if needle in name.lower():
                results.append(Path(dirpath, name).relative_to(root).as_posix())
                if len(results) >= max(1, min(limit, 50)):
                    return {"results": results}
    return {"results": results}


# ── 静态文件(web/dist 存在时,装配时挂载)──────────────────────────────

_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_web_server(
    agent: Agent,
    work_dir: Path,
    mcp_server_configs: list | None = None,
    providers: list | None = None,
) -> FastAPI:
    """装配入口:由 __main__ 的 --web 路径调用(装配同 TUI,Q5)。"""
    global STATE
    STATE = ServerState(agent, work_dir, providers=providers)
    if _DIST.exists():
        app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="static")

    @app.on_event("startup")
    async def _startup() -> None:
        # MCP 初始化(镜像 app.on_mount:连接 + 注册,同 uvicorn loop)
        if not mcp_server_configs:
            return
        from archcode.mcp import MCPManager

        manager = MCPManager()
        manager.load_configs(mcp_server_configs)
        errors, successes = await manager.register_all_tools(agent._tool_registry)
        STATE.mcp_manager = manager  # type: ignore[attr-defined]
        for name, count in successes:
            log.info("[MCP] %s: %d tools", name, count)
        for err in errors:
            log.warning("[MCP] %s", err)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        manager = getattr(STATE, "mcp_manager", None) if STATE else None
        if manager is not None:
            await manager.shutdown()
        if STATE._session is not None:
            STATE._session.close()

    return app


def run_web(
    agent: Agent,
    work_dir: Path,
    port: int,
    mcp_server_configs: list | None = None,
    providers: list | None = None,
) -> None:
    """--web 路径:装配 + uvicorn 启动(仅 127.0.0.1)。"""
    import uvicorn

    web_app = create_web_server(agent, work_dir, mcp_server_configs, providers)
    log.info("web server starting: port=%d", port)
    print(f"ArchCode Web: http://127.0.0.1:{port}", file=sys.stderr)
    uvicorn.run(web_app, host="127.0.0.1", port=port, log_level="warning")
