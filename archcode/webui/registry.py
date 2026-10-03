"""WebUI 运行时注册表:多会话并行的三层结构(设计见 workstatus/webui-multi-session-design.md)。

- EventChannel(每运行一份):事件缓冲 + 订阅扇出——"打电话"改"电台"的核心;
- SessionRuntime(每对话一份):agent / 对话 / 会话文件(惰性落盘) / 事件通道 / 用量 / 运行锁;
- WorkspaceRuntime(每工作区一份):config / providers / session_manager / 共享的 MCP 连接与子进程;
- RuntimeRegistry(全局):工作区与会话的登记、查询、并发上限。

并发模型:单事件循环内 N 个 agent 协程;共享资源只有 MCP 连接(无状态 RPC,
天然可共享)与配置(只读)。会话级 run_lock 保证单会话串行。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterator

from archcode.agent import Agent, UsageEvent
from archcode.config import load_config
from archcode.conversation.manager import ConversationManager
from archcode.llm.client import create_client
from archcode.logctx import set_session_id
from archcode.mcp import MCPManager
from archcode.memory import SessionManager
from archcode.permissions import PermissionMode
from archcode.runtime import build_agent_runtime

logger = logging.getLogger(__name__)


class EventChannel:
    """单次运行的事件缓冲 + 订阅扇出。

    publish 追加并扇出给全部订阅者;stream(after) 先重放再跟随,直到 finish。
    事件带自增 seq,SSE 断线可用 Last-Event-ID 续传。
    """

    def __init__(self) -> None:
        self._events: list[dict] = []
        self._subscribers: list[asyncio.Queue] = []
        self._done = False
        self._seq = -1

    @property
    def done(self) -> bool:
        return self._done

    def publish(self, event: dict) -> None:
        self._seq += 1
        ev = {"seq": self._seq, **event}
        self._events.append(ev)
        for q in self._subscribers:
            q.put_nowait(ev)

    def finish(self) -> None:
        if self._done:
            return
        self._done = True
        for q in self._subscribers:
            q.put_nowait(None)

    async def stream(self, after: int = -1) -> AsyncIterator[dict | None]:
        """重放 after 之后的事件并实时跟随;运行结束时投递 None 哨兵。"""
        i = after + 1
        q: asyncio.Queue | None = None
        try:
            while True:
                if i < len(self._events):
                    yield self._events[i]
                    i += 1
                    continue
                if self._done:
                    return
                if q is None:
                    # 挂队列前的 publish 只进列表;挂上后靠 seq 去重,两路合流不丢不重
                    q = asyncio.Queue()
                    self._subscribers.append(q)
                    continue
                ev = await q.get()
                if ev is None:
                    return
                if ev["seq"] < i:
                    continue
                i = ev["seq"] + 1
                yield ev
        finally:
            if q is not None and q in self._subscribers:
                self._subscribers.remove(q)


@dataclass
class SessionRuntime:
    """每对话一份的运行实例(含 agent / 对话 / 事件通道 / 逐会话状态)。"""

    session_id: str
    workspace: "WorkspaceRuntime"
    agent: Agent
    conversation: ConversationManager
    session: Any = None  # memory.Session | None;None = 草稿(未落盘)
    usage: dict = field(
        default_factory=lambda: {
            "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "rounds": 0,
        }
    )
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    events: EventChannel | None = None  # 当前运行的事件通道(非运行态为 None)
    # 运行中收到的排队消息(TUI 同款语义:FIFO,当前任务完成后逐条执行)
    pending: deque = field(default_factory=lambda: deque())
    # 会话级模型选择(provider_name, model):None = 尚未选择(跟随全局默认);
    # 空闲时立即换 client,运行中在批次下一条消息开头生效
    model_choice: tuple[str, str] | None = None
    # 上一条消息实际生成所用的模型:换模型时据此注入"换脑通知"(LLM+UI)
    last_used_model: str | None = None
    run_task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.run_task is not None and not self.run_task.done()

    def apply_model_choice(self) -> None:
        """按 model_choice 重建 agent._client(幂等;查不到供应商抛 ValueError)。

        换的是引用不是实现:旧 client 若还被在飞请求引用,互不干扰。
        """
        if self.model_choice is None:
            return
        name, model = self.model_choice
        provider = next((p for p in self.workspace.providers if p.name == name), None)
        if provider is None:
            raise ValueError(f"provider not found: {name}")
        p = provider if model == provider.model else replace(provider, model=model)
        self.agent._client = create_client(p)
        self.agent._client.set_max_output_tokens(p.max_output_tokens)

    def record_usage(self, event) -> None:
        """单轮 LLM 用量:进会话累计;会话已落盘时同步进 .meta。"""
        self.usage["input"] += int(event.input_tokens)
        self.usage["output"] += int(event.output_tokens)
        self.usage["cache_read"] += int(event.cache_read)
        self.usage["cache_creation"] += int(event.cache_creation)
        self.usage["rounds"] += 1
        if self.session is not None:
            self.session.accumulate_usage(
                event.input_tokens, event.output_tokens,
                event.cache_read, event.cache_creation,
            )

    def todo_snapshot(self) -> list[dict]:
        """本对话注册表里 TodoWrite 的当前清单(事件附带 + 面板读取)。"""
        registry = getattr(self.agent, "_tool_registry", None)
        tool = registry.get("TodoWrite") if registry is not None else None
        store = getattr(tool, "store", None)
        return list(store.todos) if store is not None else []

    def materialize(self) -> None:
        """草稿 → 正式会话:创建会话文件并绑定(首条消息发出时调用)。"""
        if self.session is not None:
            return
        self.session = self.workspace.session_manager.create()
        self.session.bind(self.conversation)
        self.session_id = self.session.id
        set_session_id(self.session.id)
        logger.info("session materialized: %s", self.session.id)


class WorkspaceRuntime:
    """每工作区一份:配置 + 会话管理器 + 共享的 MCP 连接与子进程。"""

    def __init__(self, work_dir: Path, registry: "RuntimeRegistry | None" = None) -> None:
        self.work_dir = work_dir
        self.registry = registry  # 回引注册表:读取全局默认供应商
        self.config = load_config(None, project_dir=work_dir)
        self.providers = self.config.providers
        self.protocol = self.providers[0].protocol if self.providers else "anthropic"
        self.session_manager = SessionManager(work_dir)
        self.mcp_manager: MCPManager | None = None

    def resolve_provider(self):
        """生效供应商:注册表选中的默认供应商优先,否则配置第一个。"""
        want = self.registry.default_provider_name if self.registry is not None else None
        for p in self.providers:
            if p.name == want:
                return p
        return self.providers[0] if self.providers else None

    async def ensure_mcp(self) -> tuple[list[str], list[tuple[str, int]]]:
        """幂等连接本工作区配置的全部 MCP server(连接层共享)。"""
        if self.mcp_manager is None:
            self.mcp_manager = MCPManager()
            self.mcp_manager.load_configs(self.config.mcp_servers)
        return await self.mcp_manager.ensure_connected()

    async def build_session_runtime(
        self,
        session_id: str | None = None,
        resume_id: str | None = None,
    ) -> SessionRuntime:
        """为对话构建运行实例:agent 每对话一份,MCP 工具注册层指向共享连接。"""
        await self.ensure_mcp()
        conversation = ConversationManager()
        session = None
        if resume_id:
            restored = self.session_manager.open(resume_id)
            if restored is not None:
                session = restored.session
                conversation = restored.conversation

        provider = self.resolve_provider()
        protocol = provider.protocol if provider is not None else self.protocol
        registry, agent, _executor = build_agent_runtime(
            self.config, self.work_dir, protocol
        )
        if provider is not None and self.providers and provider is not self.providers[0]:
            # build_agent_sync 固定用配置第一个供应商建 client,这里换绑选中的
            agent._client = create_client(provider)
            agent._client.set_max_output_tokens(provider.max_output_tokens)
        if self.mcp_manager is not None:
            await self.mcp_manager.register_into(registry)

        sid = session.id if session else (session_id or f"draft-{uuid.uuid4().hex[:8]}")
        usage = {
            "input": int(getattr(session.meta, "input_tokens", 0) or 0) if session else 0,
            "output": int(getattr(session.meta, "output_tokens", 0) or 0) if session else 0,
            "cache_read": int(getattr(session.meta, "cache_read_tokens", 0) or 0) if session else 0,
            "cache_creation": int(getattr(session.meta, "cache_creation_tokens", 0) or 0) if session else 0,
            "rounds": int(getattr(session.meta, "llm_rounds", 0) or 0) if session else 0,
        }
        rt = SessionRuntime(
            session_id=sid,
            workspace=self,
            agent=agent,
            conversation=conversation,
            session=session,
            usage=usage,
        )
        # 会话级持久化状态恢复(.meta):模型选择 + 权限模式——重启/换端后仍是
        # 用户上次的选择(DSH model/selection 投影恢复的 meta 版对应实现)
        if session is not None:
            meta = session.meta
            if meta.choice_provider and meta.choice_model:
                rt.model_choice = (meta.choice_provider, meta.choice_model)
                try:
                    rt.apply_model_choice()
                except ValueError as e:
                    logger.warning("model choice restore skipped: %s", e)
                    rt.model_choice = None
            if meta.permission_mode:
                checker = getattr(agent, "_permission_checker", None)
                if checker is not None:
                    try:
                        checker.mode = PermissionMode(meta.permission_mode)
                    except ValueError:
                        logger.warning(
                            "unknown persisted permission mode: %s", meta.permission_mode
                        )
        set_session_id(sid)
        logger.info("session runtime built: %s (workspace %s, resumed=%s)", sid, self.work_dir, bool(resume_id))
        return rt

    async def shutdown(self) -> None:
        if self.mcp_manager is not None:
            try:
                await self.mcp_manager.shutdown()
            except Exception as e:
                logger.warning("[workspace] MCP 收尾失败(忽略): %s", e)


class RuntimeRegistry:
    """全局管理器:工作区与会话运行实例的登记、查询与并发上限。"""

    def __init__(self, max_concurrent_runs: int = 4) -> None:
        self.workspaces: dict[str, WorkspaceRuntime] = {}
        self.sessions: dict[str, SessionRuntime] = {}
        self.max_concurrent_runs = max_concurrent_runs
        self.default_provider_name: str | None = None  # 全局默认供应商(唯一事实源)
        # 草稿 id → 正式 id:订阅/寻址发生在落盘前时,重键后旧 id 仍要能找到会话
        self._aliases: dict[str, str] = {}

    @staticmethod
    def _key(path: str | Path) -> str:
        return str(Path(path).resolve()).lower()

    def workspace(self, path: str | Path) -> WorkspaceRuntime:
        key = self._key(path)
        ws = self.workspaces.get(key)
        if ws is None:
            ws = WorkspaceRuntime(Path(path).resolve(), registry=self)
            self.workspaces[key] = ws
            logger.info("workspace runtime created: %s", ws.work_dir)
        return ws

    def workspace_paths(self) -> list[str]:
        return [str(ws.work_dir) for ws in self.workspaces.values()]

    def register_session(self, rt: SessionRuntime) -> None:
        self.sessions[rt.session_id] = rt

    def session(self, session_id: str) -> SessionRuntime | None:
        rt = self.sessions.get(session_id)
        if rt is None:
            alias = self._aliases.get(session_id)
            if alias is not None:
                rt = self.sessions.get(alias)
        return rt

    def drop_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    def running_session_ids(self) -> list[str]:
        return [rt.session_id for rt in self.sessions.values() if rt.running]

    def running_count(self) -> int:
        return sum(1 for rt in self.sessions.values() if rt.running)

    def materialize(self, rt: SessionRuntime) -> None:
        """草稿落盘后更新注册表键(临时 id → 文件 id)。

        按对象身份找旧键——调用时 rt.session_id 已经换成文件 id,
        按新键查 dict 永远 miss,重键会被整段跳过(运行时挂在草稿键下,
        新 id 查 history/state 全部 404)。
        """
        if rt.session is None:
            return
        for key, val in self.sessions.items():
            if val is rt and key != rt.session_id:
                self.sessions.pop(key, None)
                self.sessions[rt.session_id] = rt
                self._aliases[key] = rt.session_id
                break

    async def shutdown(self) -> None:
        for ws in self.workspaces.values():
            await ws.shutdown()