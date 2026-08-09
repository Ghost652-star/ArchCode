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

    async def register_all_tools(self, registry: "ToolRegistry") -> list[str]:
        """对每个 server 串行 connect + list_tools + 注册 wrapper。

        失败 server:warning + 跳过,其他继续。
        返回错误列表(每个失败一条)。
        """
        errors: list[str] = []
        for name, config in self._configs.items():
            try:
                client = MCPClient(config)
                await client.connect()
                self._clients[name] = client

                tools = await client.list_tools()
                for tool_def in tools:
                    wrapper = MCPToolWrapper(name, tool_def, client)
                    registry.register(wrapper)

            except Exception as e:
                msg = f"MCP server '{name}': {e}"
                logger.warning(msg)
                errors.append(msg)

        return errors

    async def shutdown(self) -> None:
        """关闭所有 client,清理 _clients。"""
        for name, client in self._clients.items():
            try:
                await client.close()
            except Exception:
                logger.debug("Error closing MCP server '%s'", name, exc_info=True)
        self._clients.clear()