"""Local read-only Model Context Protocol adapter."""

from material_allocation_copilot.mcp_server.server import MCPBinding, create_mcp_server

__all__ = ["MCPBinding", "create_mcp_server"]
