"""Explicit standalone-development launcher outside the MCP adapter package."""

from __future__ import annotations


def serve_development_mcp(
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8001,
):
    """Launch the legacy standalone lane through injected MCP wire adapters."""

    from argus.authority import (
        AuthorityConfigurationError,
        adapter_execution_mode,
    )

    if adapter_execution_mode() != "standalone":
        raise AuthorityConfigurationError(
            "Development MCP standalone launch requires ARGUS_MCP_STANDALONE=true"
        )

    from argus.development_mcp_adapter import build_development_mcp_backend
    from argus.development_mcp_tools import register_standalone_tools
    from argus.mcp.server import serve_mcp

    return serve_mcp(
        transport=transport,
        host=host,
        port=port,
        backend=build_development_mcp_backend(),
        additional_registration=register_standalone_tools,
    )
