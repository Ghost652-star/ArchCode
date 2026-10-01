"""MCPManager:多 server 管理 + 注册到 ToolRegistry。

启动时串行 connect + register;某个 server 失败不影响其他。
shutdown 关闭所有 client。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from archcode.mcp.client import MCPClient
from archcode.mcp.tool_wrapper import MCPToolWrapper

if TYPE_CHECKING:
    from archcode.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class MCPManager:
    """MCP server 列表的管理器。

    生命周期:
        mgr = MCPManager()
        mgr.load_configs(servers)
        await mgr.register_all_tools(registry)  # 串行 connect + register
        ...
        await mgr.shutdown()
    """

    def __init__(self) -> None:
        self._configs: dict[str, object] = {}
        self._clients: dict[str, MCPClient] = {}

    def load_configs(self, configs: list) -> None:
        """按 name 缓存 MCPServerConfig 列表。"""
        for cfg in configs:
            self._configs[cfg.name] = cfg

    async def register_all_tools(
        self, registry: "ToolRegistry"
    ) -> tuple[list[str], list[tuple[str, int]]]:
        """对每个 server 串行 connect + list_tools + 注册 wrapper。

        失败 server:warning + 跳过,其他继续。
        返回 (errors, successes):
          - errors:    失败列表,每条 "MCP server 'name': <error>"
          - successes: 成功列表,每条 (name, tool_count)
        """
        await self.ensure_connected()
        return self.register_into(registry)

    async def ensure_connected(self) -> tuple[list[str], list[tuple[str, int]]]:
        """幂等连接全部配置的 server(已连接的跳过)。

        连接层与注册层分离:多会话并行时,同工作区共享同一批连接/子进程,
        各自的 registry 通过 register_into 挂共享 client 的工具包装。
        返回 (errors, successes),含义同 register_all_tools。
        """
        errors: list[str] = []
        successes: list[tuple[str, int]] = []
        for name, config in self._configs.items():
            if name in self._clients and self._clients[name].is_alive:
                continue
            try:
                client = MCPClient(config)
                await client.connect()
                self._clients[name] = client

                tools = await client.list_tools()
                successes.append((name, len(tools)))
            except Exception as e:
                msg = f"MCP server '{name}': {e}"
                logger.warning(msg)
                errors.append(msg)

        return errors, successes

    async def register_into(self, registry: "ToolRegistry") -> None:
        """把已连接 client 的全部工具包装注册进指定 registry(轻量,可重复调用)。

        多会话并行设计(§多会话):每个对话一份 registry,包装指向共享的
        client/子进程——连接层共享,注册层按对话隔离。
        """
        for name, client in self._clients.items():
            if not client.is_alive:
                continue
            try:
                tools = await client.list_tools()
                for tool_def in tools:
                    wrapper = MCPToolWrapper(name, tool_def, client)
                    registry.register(wrapper)
            except Exception as e:
                logger.warning("MCP server '%s' 注册工具失败: %s", name, e)

    async def shutdown(self) -> None:
        """关闭所有 client,清理 _clients。"""
        # 快照迭代:client.close() 可能回写 _clients(移除自身),直迭代会 RuntimeError
        for name, client in list(self._clients.items()):
            try:
                await client.close()
            except Exception:
                logger.debug("Error closing MCP server '%s'", name, exc_info=True)
        self._clients.clear()