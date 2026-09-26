"""ArchCode Web 服务(FastAPI):把 AgentEvent 流桥接成 SSE,供 web/ 前端消费。

装配与 TUI 完全同源(__main__ 的 _build_agent_sync + _wire_hooks + _wire_skills),
agent 零改动——Web 只是 AgentEvent 流的另一个客户端(hooks-design/webui §8.5 Q5)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

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
from archcode.memory import SessionManager

log = logging.getLogger(__name__)

# ── 全局服务状态(单进程单 agent,学习项目) ─────────────────────────────


class ServerState:
    """服务端持有的运行时:agent、会话、HITL 注册表、任务锁。"""

    def __init__(self, agent: Agent, work_dir: Path, providers: list | None = None) -> None:
        self.agent = agent
        self.work_dir = work_dir
        self.providers = providers or []  # ProviderConfig 列表(模型选择器用)
        self.session_manager = SessionManager(work_dir)
        self.conversation = ConversationManager()
        self._session = self.session_manager.create()
        self._session.bind(self.conversation)
        self._run_lock = asyncio.Lock()
        self._permissions: dict[str, asyncio.Future] = {}
        self._pending_permits: list[dict] = []  # 重连时重发的未决请求

    # ── 会话 ──

    def new_session(self) -> str:
        self.agent.clear_active_skills()
        old = self._session
        self.conversation = ConversationManager()
        self._session = self.session_manager.create()
        self._session.bind(self.conversation)
        if old is not None:
            old.close()
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

    def list_sessions(self) -> list[dict]:
        running = self._run_lock.locked()
        current_id = self._session.id if self._session else None
        sessions = self.session_manager.list_sessions()
        return [
            {
                "id": s.id,
                "created": str(getattr(s, "created_at", "")),
                "current": s.id == current_id,
                "running": running and s.id == current_id,
            }
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
        lambda e: {"success": e.success, "error": e.error, "dropped": e.dropped},
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
        return {
            "type": "tool_result",
            "tool_id": event.tool_id,
            "tool_name": event.tool_name,
            "output": event.output,
            "is_error": event.is_error,
            "elapsed": event.elapsed,
        }
    if isinstance(event, InstructionDiagnosticsEvent):
        return {
            "type": "instruction_diagnostics",
            "diagnostics": [asdict(d) for d in event.diagnostics],
        }
    mapping = _TYPE_MAP.get(type(event))
    if mapping is None:
        return {"type": "unknown", "repr": repr(event)}
    wire_type, fields = mapping
    return {"type": wire_type, **fields(event)}


def _sse(payload: dict) -> str:
    return f"event: agent\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


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
    }


@app.get("/api/sessions")
def api_sessions(workspace: str | None = None):
    """会话清单;`?workspace=<path>` 可列出其他工作区的会话(只读,v1 不切换 agent)。"""
    assert STATE is not None
    if workspace:
        from archcode.memory import SessionManager as _SM

        ws = Path(workspace)
        if not ws.exists() or ws.resolve() == STATE.work_dir.resolve():
            return STATE.list_sessions()
        other = _SM(ws)
        return [
            {"id": s.id, "created": str(getattr(s, "created_at", "")),
             "current": False, "running": False, "workspace": str(ws)}
            for s in other.list_sessions()
        ]
    return STATE.list_sessions()


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


@app.get("/api/history")
def api_history():
    """当前会话全量历史(刷新恢复用):按消息角色返回。"""
    assert STATE is not None
    out = []
    for m in STATE.conversation.history:
        entry: dict = {"role": m.role, "content": m.content}
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
            try:
                async for event in STATE.agent.run(text, STATE.conversation):
                    yield _sse(serialize_event(event))
            except Exception as e:  # 兜底:agent 内部抛错也走 SSE error
                yield _sse({"type": "error", "message": str(e)})
            finally:
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
    assert STATE is not None
    total = STATE.conversation.current_tokens()
    return {"total_tokens": total, "percent": 0.0, "segments": []}


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
    print(f"ArchCode Web: http://127.0.0.1:{port}", file=sys.stderr)
    uvicorn.run(web_app, host="127.0.0.1", port=port, log_level="warning")
