"""
MCP server for Argus.

Exposes Argus search broker tools and resources via MCP protocol.
"""

import time

from typing import Any

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

from argus.auth import AuthConfig, remote_mcp_requires_auth
from argus.broker.router import create_broker
from argus.logging import get_logger, setup_logging
from argus.mcp import tools as mcp_tools
from argus.mcp import resources as mcp_resources

logger = get_logger("mcp.server")


class StaticTokenVerifier:
    """Minimal bearer-token verifier for remote MCP transports."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self._api_key or token != self._api_key:
            return None
        return AccessToken(
            token=token,
            client_id="argus-mcp",
            scopes=["mcp"],
            expires_at=int(time.time()) + 3600,
        )


def serve_mcp(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8001):
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
    auth_config = AuthConfig.from_env()
    use_remote_auth = remote_mcp_requires_auth(transport, host)

    if use_remote_auth and not auth_config.has_caller_key():
        raise SystemExit(
            "Remote MCP requires ARGUS_API_KEY. Set it in .env or:\n"
            "  export ARGUS_API_KEY=your-key"
        )

    mcp_kwargs: dict[str, Any] = {"host": host, "port": port}
    if use_remote_auth:
        resource_server_url = f"http://{host}:{port}/mcp"
        mcp_kwargs["auth"] = AuthSettings(
            issuer_url="http://127.0.0.1",
            resource_server_url=resource_server_url,
            required_scopes=["mcp"],
        )
        mcp_kwargs["token_verifier"] = StaticTokenVerifier(auth_config.caller_api_key)

    mcp = FastMCP("argus", **mcp_kwargs)

    expose_admin_tools = transport == "stdio" or use_remote_auth

    # Register tools
    @mcp.tool()
    async def search_web(
        query: str,
        mode: str = "discovery",
        max_results: int = 10,
        session_id: str = None,
        include_attribution: bool = False,
        free_only: bool = False,
        caller: str = "mcp",
    ) -> str:
        """Search the web using the Argus broker."""
        return await mcp_tools.search_web(
            broker,
            query,
            mode,
            max_results,
            session_id,
            include_attribution,
            free_only,
            caller,
        )

    @mcp.tool()
    async def recover_url(url: str, title: str = None, domain: str = None) -> str:
        """Recover a dead, moved, or unavailable URL."""
        return await mcp_tools.recover_url(broker, url, title, domain)

    @mcp.tool()
    async def expand_links(query: str, context: str = None) -> str:
        """Expand a query with related links for discovery."""
        return await mcp_tools.expand_links(broker, query, context)

    @mcp.tool()
    async def extract_content(url: str, domain: str = None) -> str:
        """Extract clean text content from a URL. Pass domain for authenticated extraction on paywall sites."""
        return await mcp_tools.extract_content(url, domain=domain)

    @mcp.tool()
    async def valyu_answer(query: str, fast_mode: bool = False) -> str:
        """Get an AI-synthesized answer with citations. Uses Valyu Answer API ($0.10+/request)."""
        return await mcp_tools.valyu_answer(query, fast_mode=fast_mode)

    @mcp.tool()
    def argus_paths() -> str:
        """Show the resolved Argus runtime storage paths."""
        return mcp_tools.argus_paths()

    from mcp.server.fastmcp import Context as McpContext

    @mcp.tool()
    async def recover_dead_article(url: str, title: str = None, domain: str = None, ctx: McpContext = None) -> str:
        """Recover a dead article into a local report with citations."""
        return await mcp_tools.recover_dead_article(broker, url, title, domain, ctx=ctx)

    @mcp.tool()
    async def capture_site(url: str, soft_page_limit: int = 75, hard_page_limit: int = 200, ctx: McpContext = None) -> str:
        """Capture the important parts of a site and summarize them."""
        return await mcp_tools.capture_site(
            broker,
            url,
            soft_page_limit=soft_page_limit,
            hard_page_limit=hard_page_limit,
            ctx=ctx,
        )

    @mcp.tool()
    async def build_research_pack(
        topic: str,
        official_url: str = None,
        max_research_pages: int = 40,
        response_format: str = "markdown",
        ctx: McpContext = None,
    ) -> str:
        """Build a local pack with official docs plus external research.

        Set response_format="json" for a machine-readable manifest
        (run_id, status, file list with paths/sizes) — use with
        read_pack_file to pipe pack contents to another service
        (e.g. Maya POST /ingest/file) without shell access.
        """
        return await mcp_tools.build_research_pack(
            broker,
            topic,
            official_url=official_url,
            max_research_pages=max_research_pages,
            response_format=response_format,
            ctx=ctx,
        )

    if expose_admin_tools:
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
        def cookie_health() -> str:
            """Get health status of all configured cookie domains."""
            return mcp_tools.cookie_health()

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

        @mcp.resource("argus://corpus/paths")
        def corpus_paths() -> str:
            """Resolved Argus runtime storage paths."""
            return mcp_resources.corpus_paths_resource()

    # Start server
    if transport == "stdio":
        logger.info("Starting Argus MCP server (stdio)")
        mcp.run(transport="stdio")
    elif transport == "sse":
        logger.info(
            "Starting Argus MCP server (sse) on %s:%d%s",
            host,
            port,
            " with auth" if use_remote_auth else "",
        )
        mcp.run(transport="sse")
    elif transport == "streamable-http":
        logger.info(
            "Starting Argus MCP server (streamable-http) on %s:%d%s",
            host,
            port,
            " with auth" if use_remote_auth else "",
        )
        mcp.run(transport="streamable-http")
    else:
        logger.error("Unknown transport: %s", transport)
