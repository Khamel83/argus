"""
MCP server for Argus.

Exposes Argus search broker tools and resources via MCP protocol.
"""

import asyncio
from typing import Any

from argus.broker.router import create_broker
from argus.logging import get_logger, setup_logging
from argus.mcp import tools as mcp_tools
from argus.mcp import resources as mcp_resources

logger = get_logger("mcp.server")


async def serve_mcp(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8001):
    """Start the Argus MCP server.

    Args:
        transport: Transport type ("stdio" or "sse")
        host: Host for SSE transport
        port: Port for SSE transport
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        logger.error("MCP package not installed. Install with: pip install 'argus[mcp]'")
        return

    setup_logging("INFO")
    broker = create_broker()

    mcp = FastMCP("argus", version="1.0.0")

    # Register tools
    @mcp.tool()
    async def search_web(query: str, mode: str = "discovery", max_results: int = 10, session_id: str = None) -> str:
        """Search the web using the Argus broker."""
        return await mcp_tools.search_web(broker, query, mode, max_results, session_id)

    @mcp.tool()
    async def recover_url(url: str, title: str = None, domain: str = None) -> str:
        """Recover a dead, moved, or unavailable URL."""
        return await mcp_tools.recover_url(broker, url, title, domain)

    @mcp.tool()
    async def expand_links(query: str, context: str = None) -> str:
        """Expand a query with related links for discovery."""
        return await mcp_tools.expand_links(broker, query, context)

    @mcp.tool()
    def search_health() -> str:
        """Get health status of all search providers."""
        return mcp_tools.search_health(broker)

    @mcp.tool()
    def search_budgets() -> str:
        """Get budget status for all providers."""
        return mcp_tools.search_budgets(broker)

    @mcp.tool()
    async def test_provider(provider: str, query: str = "argus") -> str:
        """Smoke-test a single provider."""
        return await mcp_tools.test_provider_mcp(broker, provider, query)

    @mcp.tool()
    async def extract_content(url: str) -> str:
        """Extract clean text content from a URL."""
        return await mcp_tools.extract_content(url)

    # Register resources
    @mcp.resource("argus://providers/status")
    def provider_status() -> str:
        """Current status of all search providers."""
        return mcp_resources.provider_status_resource(broker)

    @mcp.resource("argus://providers/budgets")
    def provider_budgets() -> str:
        """Budget status for all providers."""
        return mcp_resources.provider_budgets_resource(broker)

    @mcp.resource("argus://policies/current")
    def routing_policies() -> str:
        """Current routing policies for each search mode."""
        return mcp_resources.routing_policies_resource(broker)

    # Start server
    if transport == "stdio":
        logger.info("Starting Argus MCP server (stdio)")
        await mcp.run(transport="stdio")
    elif transport == "sse":
        logger.info("Starting Argus MCP server (sse) on %s:%d", host, port)
        await mcp.run(transport="sse", host=host, port=port)
    else:
        logger.error("Unknown transport: %s", transport)
