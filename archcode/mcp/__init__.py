"""MCP(Model Context Protocol):让 Agent 通过标准协议接外部工具与数据源。

子模块:
- manager.py       MCPManager 连接 MCP server 并管理生命周期
- client.py        stdio / HTTP 传输
- tool_wrapper.py  把外部工具包装为 ArchCode Tool 接口
"""

from archcode.mcp.client import MCPClient
from archcode.mcp.manager import MCPManager
from archcode.mcp.tool_wrapper import MCPToolWrapper

__all__ = ["MCPClient", "MCPManager", "MCPToolWrapper"]