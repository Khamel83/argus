"""Stateless MCP protocol adapter for the Argus HTTP authority."""

from __future__ import annotations

import time
from typing import Any

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

from argus.auth import AuthConfig, remote_mcp_requires_auth
from argus.logging import get_logger, setup_logging

logger = get_logger("mcp.server")


class StaticTokenVerifier:
    """Minimal bearer-token verifier for remote MCP transports."""

    def __init__(self, auth_config: AuthConfig):
        self._auth_config = auth_config

    async def verify_token(self, token: str) -> AccessToken | None:
        identity = self._auth_config.identity_for_token(token)
        if identity is None:
            return None
        return AccessToken(
            token=token,
            client_id=identity,
            scopes=["mcp"],
            expires_at=int(time.time()) + 3600,
        )


def _mcp_access_token() -> AccessToken | None:
    from mcp.server.auth.middleware.auth_context import get_access_token

    return get_access_token()


def _mcp_caller_identity() -> str:
    access_token = _mcp_access_token()
    return access_token.client_id if access_token else "local-mcp"


def _mcp_caller_token() -> str | None:
    access_token = _mcp_access_token()
    return access_token.token if access_token else None


def build_mcp_backend(environ=None):
    """Build an HTTP adapter or an explicitly opted-in development backend."""
    from argus.authority import (
        AuthorityConfigurationError,
        HttpAuthorityClient,
        adapter_execution_mode,
        authority_client_config,
    )

    mode = adapter_execution_mode(environ)
    if mode == "http":
        from argus.mcp.http_adapter import HttpMcpAdapter

        return HttpMcpAdapter(
            HttpAuthorityClient(authority_client_config(environ, adapter="mcp"))
        )
    if mode == "standalone":
        from argus.broker.router import create_broker
        from argus.mcp.local_adapter import LocalMcpAdapter

        return LocalMcpAdapter(create_broker())
    raise AuthorityConfigurationError(
        "MCP requires ARGUS_AUTHORITY_URL and authority authentication; "
        "development standalone mode requires ARGUS_MCP_STANDALONE=true"
    )


def serve_mcp(
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8001,
):
    """Start MCP as a protocol adapter over the configured execution backend."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        logger.error(
            "MCP package not installed. Install with: pip install 'argus-search[mcp]'"
        )
        return

    setup_logging("INFO")
    backend = build_mcp_backend()
    auth_config = AuthConfig.from_env()
    use_remote_auth = remote_mcp_requires_auth(transport, host)
    if use_remote_auth and not auth_config.has_caller_key():
        raise SystemExit(
            "Remote MCP requires ARGUS_CALLER_CREDENTIALS_JSON or ARGUS_API_KEY."
        )

    mcp_kwargs: dict[str, Any] = {"host": host, "port": port}
    if use_remote_auth:
        mcp_kwargs["auth"] = AuthSettings(
            issuer_url="http://127.0.0.1",
            resource_server_url=f"http://{host}:{port}/mcp",
            required_scopes=["mcp"],
        )
        mcp_kwargs["token_verifier"] = StaticTokenVerifier(auth_config)
    mcp = FastMCP("argus", **mcp_kwargs)

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
        """Search through the authenticated Argus HTTP authority."""
        return await backend.search_web(
            query=query,
            mode=mode,
            max_results=max_results,
            session_id=session_id,
            include_attribution=include_attribution,
            free_only=free_only,
            caller_label=caller,
            caller_identity=_mcp_caller_identity(),
            token=_mcp_caller_token(),
        )

    @mcp.tool()
    async def recover_url(
        url: str,
        title: str = None,
        domain: str = None,
        caller: str = "mcp",
    ) -> str:
        """Recover a dead or moved URL through HTTP."""
        return await backend.recover_url(
            url,
            title,
            domain,
            caller_label=caller,
            caller_identity=_mcp_caller_identity(),
            token=_mcp_caller_token(),
        )

    @mcp.tool()
    async def expand_links(
        query: str,
        context: str = None,
        caller: str = "mcp",
    ) -> str:
        """Expand related links through HTTP."""
        return await backend.expand_links(
            query,
            context,
            caller_label=caller,
            caller_identity=_mcp_caller_identity(),
            token=_mcp_caller_token(),
        )

    @mcp.tool()
    async def extract_content(url: str, domain: str = None) -> str:
        """Extract content through the authenticated HTTP authority."""
        return await backend.extract_content(
            url,
            domain,
            caller_label="mcp",
            caller_identity=_mcp_caller_identity(),
            token=_mcp_caller_token(),
        )

    @mcp.tool()
    async def search_health() -> str:
        """Read provider health from the HTTP authority."""
        return await backend.search_health(token=_mcp_caller_token())

    @mcp.tool()
    async def search_budgets() -> str:
        """Read durable provider budgets from the HTTP authority."""
        return await backend.search_budgets(token=_mcp_caller_token())

    from argus.mcp.local_adapter import LocalMcpAdapter

    if isinstance(backend, LocalMcpAdapter):
        from argus.mcp import resources, tools
        from mcp.server.fastmcp import Context as McpContext

        @mcp.tool()
        async def valyu_answer(query: str, fast_mode: bool = False) -> str:
            return await tools.valyu_answer(query, fast_mode=fast_mode)

        @mcp.tool()
        def argus_paths() -> str:
            return tools.argus_paths()

        @mcp.tool()
        async def recover_dead_article(
            url: str,
            title: str = None,
            domain: str = None,
            caller: str = "mcp",
            ctx: McpContext = None,
        ) -> str:
            return await tools.recover_dead_article(
                backend.broker,
                url,
                title,
                domain,
                ctx=ctx,
                caller_identity=_mcp_caller_identity(),
                caller_label=caller,
            )

        @mcp.tool()
        async def capture_site(
            url: str,
            soft_page_limit: int = 75,
            hard_page_limit: int = 200,
            caller: str = "mcp",
            ctx: McpContext = None,
        ) -> str:
            return await tools.capture_site(
                backend.broker,
                url,
                soft_page_limit=soft_page_limit,
                hard_page_limit=hard_page_limit,
                ctx=ctx,
                caller_identity=_mcp_caller_identity(),
                caller_label=caller,
            )

        @mcp.tool()
        async def build_research_pack(
            topic: str,
            official_url: str = None,
            max_research_pages: int = 40,
            response_format: str = "markdown",
            caller: str = "mcp",
            ctx: McpContext = None,
        ) -> str:
            return await tools.build_research_pack(
                backend.broker,
                topic,
                official_url=official_url,
                max_research_pages=max_research_pages,
                response_format=response_format,
                ctx=ctx,
                caller_identity=_mcp_caller_identity(),
                caller_label=caller,
            )

        @mcp.tool()
        def read_pack_file(
            path: str,
            max_bytes: int = 262144,
            offset: int = 0,
        ) -> str:
            return tools.read_pack_file(
                path,
                max_bytes=max_bytes,
                offset=offset,
            )

        @mcp.tool()
        async def test_provider(
            provider: str,
            query: str = "argus",
        ) -> str:
            return await tools.test_provider_mcp(
                backend.broker,
                provider,
                query,
                caller_identity=_mcp_caller_identity(),
                caller_label="mcp-admin-smoke",
            )

        @mcp.tool()
        def cookie_health() -> str:
            return tools.cookie_health()

        @mcp.resource("argus://providers/status")
        def provider_status() -> str:
            return resources.provider_status_resource(backend.broker)

        @mcp.resource("argus://providers/budgets")
        def provider_budgets() -> str:
            return resources.provider_budgets_resource(backend.broker)

        @mcp.resource("argus://policies/current")
        def routing_policies() -> str:
            return resources.routing_policies_resource(backend.broker)

        @mcp.resource("argus://corpus/paths")
        def corpus_paths() -> str:
            return resources.corpus_paths_resource()
    else:

        @mcp.tool()
        async def recover_dead_article(
            url: str,
            title: str = None,
            domain: str = None,
            caller: str = "mcp",
        ) -> str:
            return await backend.recover_dead_article(
                url,
                title,
                domain,
                caller_label=caller,
                token=_mcp_caller_token(),
            )

        @mcp.tool()
        async def capture_site(
            url: str,
            soft_page_limit: int = 75,
            hard_page_limit: int = 200,
            caller: str = "mcp",
        ) -> str:
            return await backend.capture_site(
                url,
                soft_page_limit=soft_page_limit,
                hard_page_limit=hard_page_limit,
                caller_label=caller,
                token=_mcp_caller_token(),
            )

        @mcp.tool()
        async def build_research_pack(
            topic: str,
            official_url: str = None,
            max_research_pages: int = 40,
            response_format: str = "markdown",
            caller: str = "mcp",
        ) -> str:
            return await backend.build_research_pack(
                topic,
                official_url=official_url,
                max_research_pages=max_research_pages,
                response_format=response_format,
                caller_label=caller,
                token=_mcp_caller_token(),
            )

    if transport not in {"stdio", "sse", "streamable-http"}:
        raise SystemExit(f"Unknown MCP transport: {transport}")
    logger.info(
        "Starting Argus MCP server (%s)%s",
        transport,
        " with auth" if use_remote_auth else "",
    )
    mcp.run(transport=transport)
