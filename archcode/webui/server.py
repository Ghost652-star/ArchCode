"""ArchCode Web 服务(FastAPI):多会话并行 + SSE 订阅。

架构(webui-multi-session-design.md):
- RuntimeRegistry 管理工作区(每目录一份共享资源:MCP 连接/子进程、会话管理器)
  与会话运行实例(每对话一份:agent/对话/事件通道/逐会话状态);
- 对话运行由 asyncio 任务承载,不挂任何 HTTP 请求——浏览器断开不影响运行;
- 前端按会话订阅事件(GET /api/events/{sid},支持 seq 续传)。

装配与 TUI 同源:每对话运行时由 archcode/runtime.py 的工厂构建。
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
from dataclasses import asdict, replace
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from archcode.agent import (
    CompactFinished,
    CompactProgress,
    CompactStarted,
    PermissionRequest,
    ToolResultEvent,
    UsageEvent,
)
from archcode.conversation.models import estimate_tokens
from archcode.llm.client import create_client
from archcode.logctx import set_session_id
from archcode.memory import SessionManager
from archcode.runtime import build_agent_runtime
from archcode.webui.registry import EventChannel, RuntimeRegistry, SessionRuntime

log = logging.getLogger(__name__)


# ── 全局服务状态 ─────────────────────────────────────────────────────────


class ServerState:
    """Web 服务全局状态:运行注册表 + HITL 桥 + 默认供应商。"""

    def __init__(self, providers: list | None = None) -> None:
        self.providers = providers or []
        self.registry = RuntimeRegistry(max_concurrent_runs=4)
        self.registry.default_provider_name = (
            providers[0].name if providers else None
        )
        self._permissions: dict[str, asyncio.Future] = {}
        self._pending_permits: list[dict] = []  # 重连时重发的未决请求

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
STARTUP_WORK_DIR: Path | None = None

app = FastAPI(title="ArchCode Web")


def _registry() -> RuntimeRegistry:
    assert STATE is not None
    return STATE.registry


def _workspace_or_error(workspace: str | None):
    """按路径取工作区运行时;缺省取第一个已注册的。"""
    reg = _registry()
    if workspace:
        return reg.workspace(workspace)
    paths = reg.workspace_paths()
    if not paths:
        raise HTTPException(400, "尚未注册任何工作区")
    return reg.workspace(paths[0])


def _session_or_error(session_id: str) -> SessionRuntime:
    rt = _registry().session(session_id)
    if rt is None:
        raise HTTPException(404, f"会话未打开或不存在: {session_id}")
    return rt


# ── 事件序列化(AgentEvent → wire JSON)──────────────────────────────────


_TYPE_MAP = {
    "StreamText": lambda e: {"text": e.text},
    "ThinkingText": lambda e: {"text": e.text},
    "ToolUseEvent": lambda e: {
        "tool_id": e.tool_id,
        "tool_name": e.tool_name,
        "arguments": e.arguments,
    },
    "TurnComplete": lambda e: {"turn": e.turn},
    "ErrorEvent": lambda e: {"message": e.message},
    "LoopComplete": lambda e: {"total_turns": e.total_turns, "text": e.text},
    "UsageEvent": lambda e: {
        "input_tokens": e.input_tokens,
        "output_tokens": e.output_tokens,
        "cache_read": e.cache_read,
        "cache_creation": e.cache_creation,
    },
    "RetryEvent": lambda e: {"reason": e.reason, "wait": e.wait},
    "CompactStarted": lambda e: {"mode": e.mode},
    "CompactProgress": lambda e: {"delta": e.delta, "total_chars": e.total_chars},
    "CompactFinished": lambda e: {
        "success": e.success,
        "error": e.error,
        "dropped": e.dropped,
        "summary_preview": e.summary_preview,
    },
    "InstructionDiagnosticsEvent": lambda e: {
        "diagnostics": [asdict(d) for d in e.diagnostics]
    },
}

# AgentEvent 类名 → wire type(与旧前端的 wire 协议一致)
_WIRE_NAMES = {
    "StreamText": "text",
    "ThinkingText": "thinking",
    "ToolUseEvent": "tool_use",
    "ToolResultEvent": "tool_result",
    "PermissionRequest": "permission_request",
    "TurnComplete": "turn_complete",
    "ErrorEvent": "error",
    "InstructionDiagnosticsEvent": "instruction_diagnostics",
    "LoopComplete": "loop_complete",
    "UsageEvent": "usage",
    "RetryEvent": "retry",
    "CompactStarted": "compact_started",
    "CompactProgress": "compact_progress",
    "CompactFinished": "compact_finished",
}


def serialize_event(event) -> dict:
    """AgentEvent → wire JSON dict;HITL 权限请求注册进全局 future 桥。"""
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
    if isinstance(event, ToolResultEvent):
        return {
            "type": "tool_result",
            "tool_id": event.tool_id,
            "tool_name": event.tool_name,
            "output": event.output,
            "is_error": event.is_error,
            "elapsed": event.elapsed,
        }
    cls = type(event).__name__
    maker = _TYPE_MAP.get(cls)
    if maker is None:
        return {"type": "unknown", "repr": repr(event)}
    return {"type": _WIRE_NAMES[cls], **maker(event)}


def _sse(payload: dict) -> str:
    seq = payload.get("seq")
    id_line = f"id: {seq}\n" if isinstance(seq, int) else ""
    return f"{id_line}event: agent\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


# ── 端点:状态 / 工作区 ──────────────────────────────────────────────────


@app.get("/api/state")
def api_state():
    reg = _registry()
    return {
        "running_session_ids": reg.running_session_ids(),
        "workspaces": reg.workspace_paths(),
        "max_concurrent_runs": reg.max_concurrent_runs,
    }


@app.post("/api/workspaces")
def api_workspace_register(body: dict):
    """注册工作区(惰性创建运行时;幂等)。前端启动时同步 localStorage 清单。"""
    path = str((body or {}).get("path", "")).strip()
    if not path:
        raise HTTPException(400, "path is required")
    target = Path(path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(400, f"目录不存在: {path}")
    ws = _registry().workspace(target)
    return {"ok": True, "workspaces": _registry().workspace_paths(), "work_dir": str(ws.work_dir)}


# ── 端点:会话管理 ───────────────────────────────────────────────────────


def _session_rows(workspace_runtime, running_ids: set[str]) -> list[dict]:
    reg = _registry()
    rows = []
    for s in workspace_runtime.session_manager.list_sessions():
        rt = reg.session(s.id)
        rows.append(
            {
                "id": s.id,
                "title": s.title,
                "message_count": s.message_count,
                "last_active_ms": s.last_active_ms,
                "created": str(getattr(s, "created_at", "")),
                "running": s.id in running_ids,
                "workspace": str(workspace_runtime.work_dir),
            }
        )
    rows.sort(key=lambda r: r["last_active_ms"], reverse=True)
    return rows


@app.get("/api/sessions")
def api_sessions(workspace: str | None = None):
    """列出工作区的会话(running 标记来自注册表)。workspace 缺省 = 全部已注册工作区分组返回。"""
    reg = _registry()
    running_ids = set(reg.running_session_ids())
    if workspace:
        ws = reg.workspace(workspace)
        return {"workspace": str(ws.work_dir), "sessions": _session_rows(ws, running_ids)}
    grouped = [
        {"workspace": str(ws.work_dir), "sessions": _session_rows(ws, running_ids)}
        for ws in reg.workspaces.values()
    ]
    return {"groups": grouped}


@app.post("/api/sessions")
async def api_new_session(body: dict):
    """开新对话:创建草稿运行实例(不落盘,首条消息才创建会话文件)。"""
    path = str((body or {}).get("workspace", "")).strip()
    if not path:
        raise HTTPException(400, "workspace is required")
    ws = _registry().workspace(Path(path))
    rt = await ws.build_session_runtime()
    _registry().register_session(rt)
    return {"session_id": rt.session_id, "workspace": str(ws.work_dir)}


@app.post("/api/sessions/{session_id}/resume")
async def api_resume_session(session_id: str, workspace: str | None = None):
    """打开会话:确保其 SessionRuntime 存在(从磁盘恢复)。"""
    if _registry().session(session_id) is not None:
        return {"ok": True, "session_id": session_id}
    ws = _workspace_or_error(workspace)
    rt = await ws.build_session_runtime(resume_id=session_id)
    if rt.session is None or rt.session.id != session_id:
        if rt.session is not None:
            rt.session.close()
        _registry().drop_session(rt.session_id)
        raise HTTPException(404, f"session not found: {session_id}")
    _registry().register_session(rt)
    return {"ok": True, "session_id": session_id}


@app.post("/api/sessions/{session_id}/rename")
def api_rename_session(session_id: str, body: dict, workspace: str | None = None):
    title = str((body or {}).get("title", "")).strip()
    if not title:
        raise HTTPException(400, "title is required")
    ws = _workspace_or_error(workspace)
    # 草稿 id 也要能用:注册表别名解析到落盘后的正式 id(.meta 按正式 id 命名)
    rt = _registry().session(session_id)
    effective_id = rt.session_id if rt is not None else session_id
    if not ws.session_manager.rename(effective_id, title):
        raise HTTPException(404, f"session not found: {session_id}")
    if rt is not None and rt.session is not None:
        rt.session.meta.title = title  # 同步内存态,防 _touch_meta 把旧名写回
    return {"ok": True}


@app.delete("/api/sessions/{session_id}")
def api_delete_session(session_id: str, workspace: str | None = None):
    ws = _workspace_or_error(workspace)
    rt = _registry().session(session_id)
    effective_id = rt.session_id if rt is not None else session_id
    if rt is not None:
        if rt.running:
            raise HTTPException(409, "cannot delete the running session")
        if rt.session is not None:
            rt.session.close()  # 先关句柄,Windows 下不关无法删除
        _registry().drop_session(effective_id)
    if not ws.session_manager.delete(effective_id):
        raise HTTPException(404, f"session not found: {session_id}")
    return {"ok": True}


@app.get("/api/sessions/search")
def api_sessions_search(workspace: str | None = None, q: str = "", limit: int = 8):
    """跨会话内容搜索:扫指定工作区的 .jsonl 正文(每文件前 2MB)。"""
    ws = _workspace_or_error(workspace)
    needle = q.strip().lower()
    if len(needle) < 2:
        return {"results": []}
    results: list[dict] = []
    for meta in ws.session_manager.list_sessions():
        path = ws.session_manager.sessions_dir / f"{meta.id}.jsonl"
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


# ── 端点:对话运行(chat 启动式 + 事件订阅 + 中断)──────────────────────


async def _run_session_message(rt: SessionRuntime, text: str) -> None:
    """会话运行任务:事件进通道,浏览器断开不影响;结束自行收尾。

    批次语义(TUI 排队同款,app.py _drain_submitted_inputs):首条跑完后若
    rt.pending 有排队消息,FIFO 逐条继续,共用同一条事件通道(前端订阅
    不断线);出错或用户中止时放弃剩余队列。
    """
    assert STATE is not None
    async with rt.lock:
        if rt.events is None or rt.events.done:
            rt.events = EventChannel()  # 新一轮:复用早订阅挂上的通道,已结束才换新
        try:
            was_draft = rt.session is None
            rt.materialize()
            _registry().materialize(rt)
            if was_draft:
                # 草稿键已换正式 id:广播给订阅方同步换 id,后续 state/history 按新 id 才能找到
                rt.events.publish({"type": "session_renamed", "session_id": rt.session_id})
            cur = text
            while True:
                # 会话级模型选择:每条消息开头对齐(空闲切换已在端点即时生效;
                # 运行中切换在这里落地——当前这条用原模型跑完,下一条换新)
                if rt.model_choice is not None:
                    pname, pmodel = rt.model_choice
                    if getattr(rt.agent._client, "model", "") != pmodel:
                        try:
                            _apply_session_model(rt, pname, pmodel)
                        except ValueError as e:
                            rt.model_choice = None
                            rt.events.publish({"type": "notice", "text": f"模型切换失败：{e}"})
                rt.agent._abort_event.clear()  # 每条消息重置中断(同 TUI)
                # 换脑通知:实际生成模型变了(会话级切换/重置)时,往对话注入
                # 一条持久化的 user 通知——LLM 知道上文换过脑,UI 渲染成
                # 分隔条(DSH modelSwitchNotice 同款);首条消息不发。
                cur_model = getattr(rt.agent._client, "model", "") or ""
                if rt.last_used_model is not None and cur_model != rt.last_used_model:
                    rt.conversation.add_user(
                        f"<model-switch>模型已切换：{rt.last_used_model} → {cur_model}</model-switch>"
                    )
                    rt.events.publish(
                        {
                            "type": "model_switched",
                            "from": rt.last_used_model,
                            "to": cur_model,
                        }
                    )
                rt.last_used_model = cur_model or rt.last_used_model
                turn_usage = {
                    "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "rounds": 0,
                }
                try:
                    async for event in rt.agent.run(cur, rt.conversation):
                        if isinstance(event, UsageEvent):
                            rt.record_usage(event)
                            turn_usage["input"] += int(event.input_tokens)
                            turn_usage["output"] += int(event.output_tokens)
                            turn_usage["cache_read"] += int(event.cache_read)
                            turn_usage["cache_creation"] += int(event.cache_creation)
                            turn_usage["rounds"] += 1
                        payload = serialize_event(event)
                        if isinstance(event, ToolResultEvent) and event.tool_name == "TodoWrite":
                            payload["todos"] = rt.todo_snapshot()
                        rt.events.publish(payload)
                    if turn_usage["rounds"] > 0:
                        total = sum(
                            turn_usage[k]
                            for k in ("input", "output", "cache_read", "cache_creation")
                        )
                        rt.events.publish({"type": "turn_usage", **turn_usage, "total": total})
                except Exception as e:
                    rt.events.publish({"type": "error", "message": str(e)})
                    if rt.pending:
                        dropped = len(rt.pending)
                        rt.pending.clear()
                        rt.events.publish(
                            {"type": "notice", "text": f"本轮出错，队列剩余 {dropped} 条已放弃"}
                        )
                    break
                if rt.agent._abort_event.is_set():
                    # 用户按停止 = 整批停:清空剩余队列并告知
                    if rt.pending:
                        dropped = len(rt.pending)
                        rt.pending.clear()
                        rt.events.publish(
                            {"type": "notice", "text": f"已中止，队列剩余 {dropped} 条已放弃"}
                        )
                    break
                if rt.pending:
                    cur = rt.pending.popleft()
                    continue
                break
        finally:
            pending = list(STATE._permissions.items())
            STATE._pending_permits = [
                {"request_id": rid} for rid, _ in pending
            ]
            rt.events.finish()
            rt.run_task = None


@app.post("/api/chat")
async def api_chat(body: dict):
    """启动一次对话运行(立即返回);运行中再发=入队(FIFO,批尾逐条执行)。"""
    assert STATE is not None
    reg = _registry()
    sid = str((body or {}).get("session_id", "")).strip()
    text = str((body or {}).get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text is required")
    rt = reg.session(sid)
    if rt is None:
        raise HTTPException(404, f"会话未打开: {sid}")
    if rt.running:
        if len(rt.pending) >= 10:
            raise HTTPException(429, "队列已满(10 条),等当前任务完成后再发")
        rt.pending.append(text)
        if rt.events is not None:
            rt.events.publish({"type": "queued", "position": len(rt.pending), "text": text[:80]})
        return {
            "ok": True,
            "session_id": rt.session_id,
            "queued": True,
            "position": len(rt.pending),
        }
    if reg.running_count() >= reg.max_concurrent_runs:
        raise HTTPException(429, f"并发运行已达上限({reg.max_concurrent_runs})")

    rt.run_task = asyncio.create_task(
        _run_session_message(rt, text), name=f"run-{sid}"
    )
    return {"ok": True, "session_id": rt.session_id}


def _default_model_pair() -> tuple[str, str]:
    """全局默认(供应商名, 模型名)。"""
    assert STATE is not None
    name = _registry().default_provider_name or ""
    provider = next((p for p in STATE.providers if p.name == name), None)
    return (name, provider.model if provider else "")


def _apply_session_model(rt: SessionRuntime, provider_name: str, model: str) -> None:
    """把会话的模型选择落到 agent._client。查不到供应商抛 ValueError。"""
    provider = next((p for p in rt.workspace.providers if p.name == provider_name), None)
    if provider is None and STATE is not None:
        provider = next((p for p in STATE.providers if p.name == provider_name), None)
    if provider is None:
        raise ValueError(f"provider not found: {provider_name}")
    p = provider if model == provider.model else replace(provider, model=model)
    rt.agent._client = create_client(p)
    rt.agent._client.set_max_output_tokens(p.max_output_tokens)


@app.get("/api/sessions/{session_id}/model")
def api_session_model_get(session_id: str):
    """当前会话生效模型:会话级选择优先,否则跟随全局默认。"""
    rt = _session_or_error(session_id)
    default_pair = _default_model_pair()
    if rt.model_choice is not None:
        name, model = rt.model_choice
        return {
            "provider": name,
            "model": model,
            "override": (name, model) != default_pair,
        }
    return {
        "provider": default_pair[0],
        "model": getattr(rt.agent._client, "model", "") or default_pair[1],
        "override": False,
    }


@app.post("/api/sessions/{session_id}/model")
def api_session_model_set(session_id: str, body: dict):
    """切换本会话模型(不影响其他会话;provider 为空=重置回全局默认)。

    空闲会话立即重建 client;运行中的会话当前这条消息用原模型跑完,
    批次下一条消息开头生效。
    """
    assert STATE is not None
    rt = _session_or_error(session_id)
    provider_name = str((body or {}).get("provider", "")).strip()
    model = str((body or {}).get("model", "")).strip()
    if provider_name and not model:
        raise HTTPException(400, "model is required with provider")
    if not provider_name:
        provider_name, model = _default_model_pair()
    try:
        if not rt.running:
            _apply_session_model(rt, provider_name, model)
    except ValueError as e:
        raise HTTPException(404, str(e))
    rt.model_choice = (provider_name, model)
    return {
        "ok": True,
        "provider": provider_name,
        "model": model,
        "applied": not rt.running,
        "override": (provider_name, model) != _default_model_pair(),
    }


@app.get("/api/events/{session_id}")
async def api_events(session_id: str, request: Request, after: int = -1):
    """订阅会话事件流(SSE):重放 after 之后的事件,实时跟随到运行结束。

    断线续传靠 Last-Event-ID 头(EventSource 自动带),查询参数 after 仅调试用。
    流末尾发 done 哨兵——浏览器 EventSource 对正常结束的流会自动重连
    (onerror 且 readyState=CONNECTING),没有 done 客户端就永远等不到结束。
    """
    rt = _registry().session(session_id)
    if rt is None:

        async def _idle():
            yield _sse({"type": "idle"})
            yield _sse({"type": "done"})

        return StreamingResponse(
            _idle(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )
    if rt.events is None:
        # 先于首条消息订阅:挂上空通道等运行开张(否则拿到即时 idle,订阅形同虚设)
        rt.events = EventChannel()
    if after < 0:
        lei = request.headers.get("last-event-id", "")
        if lei.isdigit():
            after = int(lei)

    async def _stream():
        async for ev in rt.events.stream(after):
            yield _sse(ev)
        yield _sse({"type": "done"})

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/abort")
async def api_abort(body: dict):
    """中断指定会话的运行(协作式:设中断事件,运行任务自行收尾)。"""
    assert STATE is not None
    sid = str((body or {}).get("session_id", "")).strip()
    rt = _registry().session(sid)
    if rt is None:
        raise HTTPException(404, f"会话未打开: {sid}")
    log.info("abort requested (web): session=%s", sid)
    rt.agent._abort_event.set()
    return {"ok": True}


@app.get("/api/history")
def api_history(session_id: str):
    """该会话的全量历史(刷新恢复用);内部注入的 user 消息不进 UI。"""
    rt = _session_or_error(session_id)
    internal_prefixes = (
        "<system-reminder>",
        "<会话恢复材料>",
        "[恢复提示]",
        "<memory-context>",  # 长期记忆索引(非持久化注入段)
        "<active-skills>",  # 激活 Skill 钉住段(同上)
    )
    out = []
    for m in rt.conversation.history:
        if m.role == "user" and m.content.startswith(internal_prefixes):
            continue
        entry: dict = {"role": m.role, "content": m.content, "created_at": m.created_at}
        if m.tool_uses:
            entry["tool_uses"] = [asdict(u) for u in m.tool_uses]
        if m.tool_results:
            entry["tool_results"] = [asdict(r) for r in m.tool_results]
        out.append(entry)
    return out


@app.get("/api/context")
def api_context(session_id: str):
    """上下文占用(§13-A4):percent = 当前 token / 窗口(provider 可配,缺省 128k)。

    breakdown 为启发式分段估算,供 composer 旁的占用卡做分类着色:
    系统提示词 / 内建工具 schema / MCP(延迟)工具 schema / 激活 Skill 钉住段 /
    记忆索引段 / 消息(= 总量减去其余,保证闭合)。数字是估算值。
    """
    rt = _session_or_error(session_id)
    total = rt.conversation.current_tokens()
    provider = rt.workspace.resolve_provider()
    window = int(getattr(provider, "context_window", 0) or 0) or 131072
    percent = min(1.0, total / window) if window > 0 else 0.0

    def _est(text: str | None) -> int:
        return int(len(text or "") / 3.5)

    system = _est(getattr(rt.agent, "_system_prompt", ""))
    tools_builtin = tools_mcp = 0
    tool_registry = getattr(rt.agent, "_tool_registry", None)
    if tool_registry is not None:
        for tool in getattr(tool_registry, "_tools", {}).values():
            try:
                est = _est(json.dumps(tool.get_schema(), ensure_ascii=False))
            except Exception:
                continue
            if getattr(tool, "should_defer", False):
                tools_mcp += est
            else:
                tools_builtin += est
    conversation = rt.conversation
    skills_msg = getattr(conversation, "_active_skills_message", None)
    memory_msg = getattr(conversation, "_memory_context_message", None)
    skills = _est(skills_msg.content if skills_msg is not None else None)
    memory = _est(memory_msg.content if memory_msg is not None else None)
    fixed = system + tools_builtin + tools_mcp + skills + memory
    if getattr(conversation, "baseline_tokens", 0) > 0 and fixed > 0:
        # 真值锚定(供应商回了 usage):分段启发式的字符密度与真实分词有偏差,
        # 直接做残差会把"消息"段永久钳到 0。按真值总量等比校准各固定段,
        # 消息段由真值减校准后固定段推出,保持六段闭合。
        pins = ("<memory-context>", "<active-skills>", "<system-reminder>")
        bare_msgs = estimate_tokens(
            [m for m in conversation.history if not m.content.startswith(pins)]
        )
        denom = fixed + bare_msgs
        if denom > 0:
            k = total / denom
            system = int(system * k)
            tools_builtin = int(tools_builtin * k)
            tools_mcp = int(tools_mcp * k)
            skills = int(skills * k)
            memory = int(memory * k)
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
def api_usage(session_id: str):
    """会话累计 LLM 用量(.meta 索引持久化,resume 后可恢复)。"""
    rt = _session_or_error(session_id)
    u = rt.usage
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


@app.get("/api/todo")
def api_todo(session_id: str):
    """当前会话任务清单(TodoWrite store 快照)。"""
    rt = _session_or_error(session_id)
    return {"todos": rt.todo_snapshot()}


@app.get("/api/tasks")
def api_tasks(session_id: str):
    """后台任务清单(TaskManager 只读快照):状态/耗时/token 用量/结果预览。"""
    rt = _session_or_error(session_id)
    manager = getattr(rt.agent, "_task_manager", None)
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


# ── 端点:以下是模型/权限/技能/子Agent/设置/文件 ──


@app.get("/api/model")
def api_model():
    assert STATE is not None
    name = STATE.registry.default_provider_name
    provider = next((p for p in STATE.providers if p.name == name), None)
    if provider is None and STATE.providers:
        provider = STATE.providers[0]
        STATE.registry.default_provider_name = provider.name
    return {
        "current": provider.model if provider else "",
        "providers": [
            {"name": p.name, "model": p.model, "protocol": p.protocol}
            for p in STATE.providers
        ],
    }


@app.post("/api/model")
def api_model_switch(body: dict):
    """切换全局默认供应商:新会话用它;空闲的已开会话立即重建 client。

    运行中的保持;有会话级模型选择(model_choice)的会话不动——那是它的选择。
    """
    assert STATE is not None
    name = str((body or {}).get("name", ""))
    provider = next((p for p in STATE.providers if p.name == name), None)
    if provider is None:
        raise HTTPException(404, f"provider not found: {name}")
    _registry().default_provider_name = name
    for rt in _registry().sessions.values():
        if rt.running or rt.model_choice is not None:
            continue
        rt.agent._client = create_client(provider)
        rt.agent._client.set_max_output_tokens(provider.max_output_tokens)
    return {"ok": True, "model": provider.model}


@app.post("/api/permission/{request_id}")
async def api_permission(request_id: str, body: dict):
    assert STATE is not None
    allowed = bool((body or {}).get("allowed", False))
    answer = (body or {}).get("answer")
    value = answer if answer is not None else allowed
    STATE.resolve_permission(request_id, value)
    return {"ok": True}


@app.get("/api/permission-mode")
def api_permission_mode_get(session_id: str):
    rt = _session_or_error(session_id)
    checker = getattr(rt.agent, "_permission_checker", None)
    return {
        "mode": checker.mode.value if checker else "default",
        "plan_mode": bool(getattr(rt.agent, "_plan_mode", False)),
    }


@app.post("/api/permission-mode")
def api_permission_mode(body: dict):
    assert STATE is not None
    from archcode.permissions import PermissionMode

    sid = str((body or {}).get("session_id", "")).strip()
    mode = str((body or {}).get("mode", "")).strip()
    if mode not in ("default", "accept", "bypass"):
        raise HTTPException(400, f"unknown mode: {mode}")
    rt = _registry().session(sid)
    if rt is None:
        raise HTTPException(404, f"会话未打开: {sid}")
    checker = getattr(rt.agent, "_permission_checker", None)
    if checker is not None:
        checker.mode = PermissionMode(mode)
    return {"ok": True, "mode": mode}


# ── 端点:Skills / 子 Agent(按工作区)─────────────────────────────────


@app.get("/api/skills")
def api_skills(workspace: str | None = None):
    from archcode.skills import SkillLoader

    ws = _workspace_or_error(workspace)
    loader = SkillLoader(work_dir=ws.work_dir)
    return [
        {
            "name": m.name,
            "description": m.description,
            "source": m.source,
            "path": str(m.path),
            "is_directory": m.is_directory,
        }
        for m in loader.scan().values()
    ]


@app.get("/api/agents")
def api_agents(workspace: str | None = None):
    from archcode.agents.loader import AgentLoader

    ws = _workspace_or_error(workspace)
    loader = AgentLoader(work_dir=ws.work_dir)
    loader.load_all()
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
            "system_prompt": d.system_prompt,
        }
        for d in loader.manifests().values()
    ]


_AGENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")


def _agents_dir_for(workspace: str, scope: str) -> Path:
    from archcode.paths import application_agents_dir, project_agents_dir

    if scope == "project":
        return project_agents_dir(_workspace_or_error(workspace).work_dir)
    if scope == "user":
        return application_agents_dir()
    raise HTTPException(400, f"unknown scope: {scope}")


def _yaml_str(v: str) -> str:
    return json.dumps(v, ensure_ascii=False)


@app.post("/api/agents/{scope}")
async def api_agents_save(scope: str, body: dict, workspace: str | None = None):
    """新建/覆盖一个子 agent 定义(渲染 frontmatter Markdown 写入作用域目录)。"""
    ws = _workspace_or_error(workspace)
    body = body or {}
    name = str(body.get("agent_type", "")).strip()
    if not _AGENT_NAME_RE.match(name):
        raise HTTPException(400, "名称必须以字母开头,仅含字母/数字/_/-")
    description = str(body.get("when_to_use", "")).strip()
    system_prompt = str(body.get("system_prompt", "")).strip()
    if not description:
        raise HTTPException(400, "描述(选用依据)必填")
    if not system_prompt:
        raise HTTPException(400, "系统提示词必填")
    tools = [str(t).strip() for t in (body.get("tools") or []) if str(t).strip()]
    disallowed = [
        str(t).strip() for t in (body.get("disallowed_tools") or []) if str(t).strip()
    ]
    model = str(body.get("model", "")).strip()
    try:
        max_turns = int(body.get("max_turns", 50))
    except (TypeError, ValueError):
        raise HTTPException(400, "轮次预算必须是正整数")
    if not 1 <= max_turns <= 500:
        raise HTTPException(400, "轮次预算取值 1~500")
    permission_mode = str(body.get("permission_mode", "default"))
    if permission_mode not in ("default", "acceptEdits", "dontAsk"):
        raise HTTPException(400, "权限模式非法")
    background = bool(body.get("background", False))

    lines = ["---", f"name: {_yaml_str(name)}", f"description: {_yaml_str(description)}"]
    if tools:
        lines.append("tools: [" + ", ".join(_yaml_str(t) for t in tools) + "]")
    if disallowed:
        lines.append(
            "disallowedTools: [" + ", ".join(_yaml_str(t) for t in disallowed) + "]"
        )
    if model and model != "inherit":
        lines.append(f"model: {_yaml_str(model)}")
    if max_turns != 50:
        lines.append(f"maxTurns: {max_turns}")
    if permission_mode != "default":
        lines.append(f"permissionMode: {permission_mode}")
    if background:
        lines.append("background: true")
    lines.append("---")
    md = "\n".join(lines) + "\n\n" + system_prompt + "\n"

    target_dir = _agents_dir_for(workspace, scope)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{name}.md"
    path.write_text(md, encoding="utf-8")
    log.info("agent definition saved: %s", path)
    return {"ok": True, "path": str(path), "restart_required": True}


@app.delete("/api/agents/{scope}/{agent_type}")
async def api_agents_delete(scope: str, agent_type: str, workspace: str | None = None):
    from archcode.agents.loader import AgentLoader

    ws = _workspace_or_error(workspace)
    loader = AgentLoader(work_dir=ws.work_dir)
    loader.load_all()
    defn = loader.get(agent_type)
    if defn is None:
        raise HTTPException(404, f"agent not found: {agent_type}")
    scope_dir = _agents_dir_for(workspace, scope).resolve()
    if defn.file_path is None or not defn.file_path.resolve().is_relative_to(scope_dir):
        raise HTTPException(400, "只能删除当前作用域目录内的定义(内置定义不可删)")
    defn.file_path.unlink()
    log.info("agent definition deleted: %s", defn.file_path)
    return {"ok": True, "restart_required": True}


# ── 端点:设置(config.yaml 读写,按工作区)────────────────────────────


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


def _settings_workspace(workspace: str | None) -> Path:
    return _workspace_or_error(workspace).work_dir


@app.get("/api/settings/{scope}")
def api_settings_get(scope: str, workspace: str | None = None):
    path = _scope_path(scope, _settings_workspace(workspace))
    if not path.exists():
        return {"path": str(path), "data": {}, "exists": False}
    data = _yaml.load(path.read_text(encoding="utf-8"))
    return {"path": str(path), "data": data if data is not None else {}, "exists": True}


@app.put("/api/settings/{scope}")
def api_settings_put(scope: str, body: dict, workspace: str | None = None):
    path = _scope_path(scope, _settings_workspace(workspace))
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


# ── 端点:工作区文件(只读浏览,圈定在工作区内)────────────────────────


_MAX_ENTRIES = 2000
_MAX_LINES = 2000
_MAX_BYTES = 512 * 1024


def _confine(workspace: str | None, rel: str) -> Path:
    """相对路径 → 工作区内的绝对路径;越界 403、不存在 404。空串 = 根。"""
    ws = _workspace_or_error(workspace)
    rel = (rel or "").replace("\\", "/").strip("/")
    root = ws.work_dir.resolve()
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
def api_files(workspace: str | None = None, path: str = ""):
    target = _confine(workspace, path)
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
def api_file(workspace: str | None = None, path: str = ""):
    target = _confine(workspace, path)
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


_SEARCH_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".archcode",
    ".pytest_cache", ".idea", ".cursor", ".codegraph", "dist", "build",
}
_SEARCH_SCAN_CAP = 20000


@app.get("/api/files/search")
def api_files_search(workspace: str | None = None, q: str = "", limit: int = 20):
    ws = _workspace_or_error(workspace)
    needle = q.strip().lower()
    if not needle:
        return {"results": []}
    root = ws.work_dir.resolve()
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


@app.post("/api/workspace/pick")
def api_workspace_pick():
    """弹原生目录选择框(tkinter),返回所选绝对路径。

    服务仅绑定 127.0.0.1,与浏览器同机——所以服务端弹的窗口就是用户屏幕上
    的窗口。tkinter 建在一次性线程里;同步端点跑在 FastAPI 线程池,阻塞不占
    事件循环。
    """
    import threading

    def _pick() -> None:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            picked["path"] = filedialog.askdirectory(title="选择工作区文件夹") or ""
        finally:
            root.destroy()

    picked: dict = {}
    try:
        t = threading.Thread(target=_pick, daemon=True)
        t.start()
        t.join()
    except Exception as e:
        raise HTTPException(500, f"目录选择器不可用: {e}")
    path = (picked.get("path") or "").strip()
    return {"ok": bool(path), "path": path or None}


# ── 装配入口 ─────────────────────────────────────────────────────────────

_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_web_server(work_dir: Path | None, config) -> FastAPI:
    """装配入口:注册启动工作区(work_dir=None = 空启动),清扫空会话,挂静态前端。"""
    global STATE, STARTUP_WORK_DIR
    STATE = ServerState(providers=config.providers)
    registry = STATE.registry
    if work_dir is not None:
        STARTUP_WORK_DIR = Path(work_dir).resolve()
        ws = registry.workspace(STARTUP_WORK_DIR)
        removed = ws.session_manager.sweep_empty()
        if removed:
            log.info("swept %d empty session file(s)", removed)
    else:
        # 空启动:工作区由前端注册(localStorage 清单 / 侧栏添加)
        STARTUP_WORK_DIR = None
        log.info("no startup workspace: waiting for frontend registration")
    if _DIST.exists():
        app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="static")

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await registry.shutdown()

    return app


def run_web(work_dir: Path | None, config, port: int) -> None:
    """--web 路径:装配 + uvicorn 启动(仅 127.0.0.1)。work_dir=None = 空启动。"""
    import uvicorn

    create_web_server(work_dir=work_dir, config=config)
    log.info("web server starting: port=%d work_dir=%s", port, work_dir)
    print(f"ArchCode Web: http://127.0.0.1:{port}", file=sys.stderr)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")