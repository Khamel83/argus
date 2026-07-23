"""Explicit development-only MCP adapter for standalone Argus."""

from __future__ import annotations


class LocalMcpAdapter:
    """Compatibility adapter that owns a broker only in opted-in development."""

    def __init__(self, broker):
        self.broker = broker

    async def search_web(
        self,
        *,
        query,
        mode="discovery",
        max_results=10,
        session_id=None,
        include_attribution=False,
        free_only=False,
        caller_label="mcp",
        caller_identity="local-mcp",
        token=None,
    ):
        del token
        from argus.mcp import tools

        return await tools.search_web(
            self.broker,
            query,
            mode,
            max_results,
            session_id,
            include_attribution,
            free_only,
            caller_identity,
            caller_label=caller_label,
        )

    async def recover_url(
        self,
        url,
        title=None,
        domain=None,
        *,
        caller_label="mcp",
        caller_identity="local-mcp",
        token=None,
    ):
        del token
        from argus.mcp import tools

        return await tools.recover_url(
            self.broker,
            url,
            title,
            domain,
            caller_identity=caller_identity,
            caller_label=caller_label,
        )

    async def expand_links(
        self,
        query,
        context=None,
        *,
        caller_label="mcp",
        caller_identity="local-mcp",
        token=None,
    ):
        del token
        from argus.mcp import tools

        return await tools.expand_links(
            self.broker,
            query,
            context,
            caller_identity=caller_identity,
            caller_label=caller_label,
        )

    async def extract_content(
        self,
        url,
        domain=None,
        *,
        caller_label="mcp",
        caller_identity="local-mcp",
        token=None,
    ):
        del caller_label, caller_identity, token
        from argus.mcp import tools

        return await tools.extract_content(url, domain=domain)

    async def search_health(self, *, token=None):
        del token
        from argus.mcp import tools

        return tools.search_health(self.broker)

    async def search_budgets(self, *, token=None):
        del token
        from argus.mcp import tools

        return tools.search_budgets(self.broker)
