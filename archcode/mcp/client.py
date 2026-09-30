"""MCPClient:单个 MCP server 的连接包装。

两种 transport:
- stdio: 用 mcp.client.stdio.stdio_client + StdioServerParameters
- HTTP:  用 mcp.client.streamable_http.streamable_http_client + httpx.AsyncClient

生命周期模型(工作区切换的关键约束):
stdio/HTTP 这类 async 上下文由 anyio 任务组承载,**必须在进入它的同一个任务里
退出**——跨任务退出会把取消信号泄漏进别的任务(实测:切换工作区时在请求任务里
close,取消信号串进 uvicorn 的 lifespan 任务,打出 CancelledError)。

因此 connect() 启动一个**专属持有任务**(_run),全部上下文开在该任务内;
close() 只设置关闭事件并等待持有任务自行退出——事件驱动,任意任务调用都安全。
错误处理:connect 失败经 _ready 事件回传首次异常;close 幂等。
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class MCPClient:
    """单个 MCP server 的连接。

    用法:
        client = MCPClient(config)
        await client.connect()           # 启动持有任务 + 子进程/HTTP + 握手
        tools = await client.list_tools()
        result = await client.call_tool(name, args)
        await client.close()             # 通知持有任务退出(任意任务可调)
    """

    def __init__(self, config: Any) -> None:
        self.config = config
        self.name = config.name
        self._session: Any = None
        self._alive = False
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._close_evt = asyncio.Event()
        self._connect_error: BaseException | None = None

    @property
    def is_alive(self) -> bool:
        return self._alive

    async def connect(self) -> None:
        """启动持有任务并等待握手完成。失败抛首次异常,资源已由持有任务清理。"""
        if self._alive:
            return

        self._ready = asyncio.Event()
        self._close_evt = asyncio.Event()
        self._connect_error = None
        self._task = asyncio.create_task(self._run(), name=f"mcp-{self.name}")
        await self._ready.wait()
        if self._connect_error is not None:
            self._task = None
            raise self._connect_error

    async def _run(self) -> None:
        """持有任务:全部 async 上下文在本任务内进出(关闭信号驱动)。"""
        stack = AsyncExitStack()
        try:
            await stack.__aenter__()
            if self.config.is_stdio:
                read, write = await self._connect_stdio(stack)
            else:
                read, write = await self._connect_http(stack)

            session = await stack.enter_async_context(
                self._build_session(read, write)
            )
            await session.initialize()
            self._session = session
            self._alive = True
            self._ready.set()
            await self._close_evt.wait()
        except BaseException as e:  # 连接失败/关闭期取消都由 finally 兜底清理
            if not self._ready.is_set():
                self._connect_error = e
            elif not isinstance(e, asyncio.CancelledError):
                logger.debug("MCP '%s' 持有任务结束: %s", self.name, e)
        finally:
            self._alive = False
            self._session = None
            try:
                await stack.aclose()
            except RuntimeError as e:
                if "cancel scope" not in str(e):
                    logger.debug(
                        "Error closing stack for '%s'", self.name, exc_info=True
                    )
            except Exception:
                logger.debug(
                    "Error closing stack for '%s'", self.name, exc_info=True
                )
            self._ready.set()

    @staticmethod
    def _build_session(read: Any, write: Any) -> Any:
        """延迟导入 mcp SDK,避免在测试加载时强制依赖。"""
        from mcp import ClientSession
        return ClientSession(read, write)

    async def _connect_stdio(self, stack: AsyncExitStack) -> tuple[Any, Any]:
        """stdio transport:spawn 子进程,接 stderr 到 devnull。"""
        from mcp.client.stdio import StdioServerParameters, stdio_client

        assert self.config.command is not None

        # 把父进程 PATH 加进去,允许 npx 等命令工作;再覆盖声明的 env
        child_env = dict(os.environ)
        for k, v in self.config.env.items():
            child_env[k] = v

        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=child_env,
        )

        devnull = open(os.devnull, "w")
        stack.callback(devnull.close)

        read, write = await stack.enter_async_context(
            stdio_client(params, errlog=devnull)
        )
        return read, write

    async def _connect_http(self, stack: AsyncExitStack) -> tuple[Any, Any]:
        """HTTP transport:自己建 httpx.AsyncClient,传给 streamable_http_client。"""
        from mcp.client.streamable_http import streamable_http_client

        assert self.config.url is not None

        # headers 直接用 config 的
        http_client = httpx.AsyncClient(
            headers=self.config.headers,
            follow_redirects=True,
        )
        await stack.enter_async_context(http_client)

        result = await stack.enter_async_context(
            streamable_http_client(self.config.url, http_client=http_client)
        )
        return result[0], result[1]

    async def list_tools(self) -> list[Any]:
        """返回 MCP server 的工具定义列表。"""
        assert self._session is not None
        result = await self._session.list_tools()
        return list(result.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """调用 MCP 工具,返回 CallToolResult。"""
        assert self._session is not None
        return await self._session.call_tool(name, arguments)

    async def close(self) -> None:
        """关闭连接:通知持有任务退出并等待其清理完毕(幂等,任意任务可调)。"""
        if self._task is None:
            return
        self._close_evt.set()
        task = self._task
        self._task = None
        try:
            await task
        except asyncio.CancelledError:
            pass
