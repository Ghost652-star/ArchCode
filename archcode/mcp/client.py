"""MCPClient:单个 MCP server 的连接包装。

两种 transport:
- stdio: 用 mcp.client.stdio.stdio_client + StdioServerParameters
- HTTP:  用 mcp.client.streamable_http.streamable_http_client + httpx.AsyncClient

生命周期由 AsyncExitStack 管(子进程 / httpx client / ClientSession 一起清理)。
错误处理:connect() 单次尝试;close() 吞掉 cancel scope RuntimeError。
"""

from __future__ import annotations

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
        await client.connect()           # 启动子进程 / 开 HTTP 连接 + 握手
        tools = await client.list_tools()
        result = await client.call_tool(name, args)
        await client.close()             # 清理所有资源
    """

    def __init__(self, config: Any) -> None:
        self.config = config
        self.name = config.name
        self._session: Any = None
        self._stack: AsyncExitStack | None = None
        self._alive = False

    @property
    def is_alive(self) -> bool:
        return self._alive

    async def connect(self) -> None:
        """启动 transport + 握手。失败抛异常,资源自动清理。"""
        if self._alive:
            return

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        try:
            if self.config.is_stdio:
                read, write = await self._connect_stdio()
            else:
                read, write = await self._connect_http()

            session = await self._stack.enter_async_context(
                self._build_session(read, write)
            )
            await session.initialize()
            self._session = session
            self._alive = True
            logger.info("MCP server '%s' connected", self.name)
        except Exception:
            await self._cleanup_stack()
            raise

    @staticmethod
    def _build_session(read: Any, write: Any) -> Any:
        """延迟导入 mcp SDK,避免在测试加载时强制依赖。"""
        from mcp import ClientSession
        return ClientSession(read, write)

    async def _connect_stdio(self) -> tuple[Any, Any]:
        """stdio transport:spawn 子进程,接 stderr 到 devnull。"""
        from mcp.client.stdio import StdioServerParameters, stdio_client

        assert self._stack is not None
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
        self._stack.callback(devnull.close)

        read, write = await self._stack.enter_async_context(
            stdio_client(params, errlog=devnull)
        )
        return read, write

    async def _connect_http(self) -> tuple[Any, Any]:
        """HTTP transport:自己建 httpx.AsyncClient,传给 streamable_http_client。"""
        from mcp.client.streamable_http import streamable_http_client

        assert self._stack is not None
        assert self.config.url is not None

        # headers 直接用 config 的
        http_client = httpx.AsyncClient(
            headers=self.config.headers,
            follow_redirects=True,
        )
        await self._stack.enter_async_context(http_client)

        result = await self._stack.enter_async_context(
            streamable_http_client(self.config.url, http_client=http_client)
        )
        read, write = result[0], result[1]
        return read, write

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
        """关闭连接,清理所有资源。"""
        self._alive = False
        self._session = None
        await self._cleanup_stack()

    async def _cleanup_stack(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.__aexit__(None, None, None)
            except RuntimeError as e:
                if "cancel scope" in str(e):
                    logger.debug(
                        "Cancel scope cleanup (expected during shutdown): %s", e
                    )
                else:
                    raise
            except Exception:
                logger.debug(
                    "Error closing stack for '%s'", self.name, exc_info=True
                )
            self._stack = None