"""Stateless MCP presentation adapter over the authenticated HTTP authority."""

from __future__ import annotations

import json
from typing import Any

from argus.authority import HttpAuthorityClient


def _search_markdown(payload: dict[str, Any]) -> str:
    traces = payload.get("traces") or []
    providers = [
        str(trace.get("provider"))
        for trace in traces
        if trace.get("results_count", 0) > 0
    ]
    provider_text = ", ".join(providers) if providers else "none"
    cached = " (cached)" if payload.get("cached") else ""
    lines = [
        f"## Search Results: {payload.get('query', '')!r}",
        (
            f"Mode: {payload.get('mode', 'discovery')} | "
            f"{payload.get('total_results', 0)} results | "
            f"via {provider_text}{cached}"
        ),
        "",
    ]
    if payload.get("budget_warnings"):
        lines.append(
            "**Budget warnings:** "
            + "; ".join(str(item) for item in payload["budget_warnings"])
        )
        lines.append("")
    for index, result in enumerate(payload.get("results") or [], 1):
        lines.append(f"{index}. **{result.get('title') or '(no title)'}**")
        lines.append(f"   URL: {result.get('url', '')}")
        lines.append(f"   Egress: {result.get('egress') or 'unknown'}")
        attribution = result.get("score_attribution") or {}
        if attribution:
            score_text = ", ".join(
                f"{provider}: {value:.4f}"
                for provider, value in sorted(
                    attribution.items(),
                    key=lambda item: -item[1],
                )
            )
            lines.append(f"   Score attribution: {score_text}")
        if result.get("snippet"):
            lines.append(f"   {result['snippet']}")
        lines.append("")
    if payload.get("session_id"):
        lines.append(f"_Session ID: {payload['session_id']}_")
    return "\n".join(lines)


def _workflow_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"## {str(payload.get('kind', 'workflow')).replace('_', ' ').title()}",
        (
            f"Status: {payload.get('status', 'unknown')} | "
            f"Run: {payload.get('run_id', '')}"
        ),
        f"Target: {payload.get('target', '')}",
    ]
    if payload.get("error"):
        lines.append("Error: workflow failed")
    return "\n".join(lines)


class HttpMcpAdapter:
    """Translate typed MCP calls without owning execution resources."""

    def __init__(self, client: HttpAuthorityClient):
        self._client = client

    async def search_web(
        self,
        *,
        query: str,
        mode: str = "discovery",
        max_results: int = 10,
        session_id: str | None = None,
        include_attribution: bool = False,
        free_only: bool = False,
        caller_label: str = "mcp",
        caller_identity: str = "mcp",
        token: str | None = None,
    ) -> str:
        del caller_identity
        request = {
            "query": query,
            "mode": mode,
            "max_results": max_results,
            "include_attribution": include_attribution,
            "free_only": free_only,
            "caller": caller_label,
        }
        if session_id:
            request["session_id"] = session_id
        response = await self._client.search(request, token=token)
        return _search_markdown(response)

    async def recover_url(
        self,
        url: str,
        title: str | None = None,
        domain: str | None = None,
        *,
        caller_label: str = "mcp",
        caller_identity: str = "mcp",
        token: str | None = None,
    ) -> str:
        del caller_label, caller_identity
        response = await self._client.request(
            "POST",
            "/api/recover-url",
            payload={
                "url": url,
                "title": title,
                "domain": domain,
            },
            token=token,
        )
        return _search_markdown(response)

    async def expand_links(
        self,
        query: str,
        context: str | None = None,
        *,
        caller_label: str = "mcp",
        caller_identity: str = "mcp",
        token: str | None = None,
    ) -> str:
        del caller_label, caller_identity
        response = await self._client.request(
            "POST",
            "/api/expand",
            payload={
                "query": query,
                "context": context,
            },
            token=token,
        )
        return _search_markdown(response)

    async def extract_content(
        self,
        url: str,
        domain: str | None = None,
        *,
        caller_label: str = "mcp",
        caller_identity: str = "mcp",
        token: str | None = None,
    ) -> str:
        del caller_identity
        response = await self._client.request(
            "POST",
            "/api/extract",
            payload={
                "url": url,
                "domain": domain,
                "caller": caller_label,
            },
            token=token,
        )
        if response.get("error"):
            return (
                f"**Extraction failed:** {response['error']}\n"
                f"URL: {response.get('url', url)}"
            )
        metadata = []
        for label, key in (
            ("Author", "author"),
            ("Date", "date"),
            ("Words", "word_count"),
            ("Extractor", "extractor"),
            ("Egress", "egress"),
            ("Machine", "machine"),
        ):
            if response.get(key):
                metadata.append(f"{label}: {response[key]}")
        lines = [
            f"# {response.get('title') or response.get('url', url)}",
            f"URL: {response.get('url', url)}",
        ]
        if metadata:
            lines.append(" | ".join(metadata))
        lines.extend(["", response.get("text") or ""])
        return "\n".join(lines)

    async def search_health(self, *, token: str | None = None) -> str:
        response = await self._client.request(
            "GET",
            "/api/provider-health",
            token=token,
        )
        lines = ["## Search Provider Health", ""]
        for provider, status in (response.get("providers") or {}).items():
            lines.append(
                f"- **{provider}**: {status.get('effective_status', 'unknown')}"
            )
        return "\n".join(lines)

    async def search_budgets(self, *, token: str | None = None) -> str:
        response = await self._client.request(
            "GET",
            "/api/budgets",
            token=token,
        )
        lines = ["## Search Provider Budgets", ""]
        for provider, summary in (response.get("providers") or {}).items():
            lines.append(
                f"- **{provider}**: remaining={summary.get('remaining')} "
                f"estimated={summary.get('argus_estimated_charge')} "
                f"uncertain={summary.get('uncertain_charge')}"
            )
        return "\n".join(lines)

    async def recover_dead_article(
        self,
        url: str,
        title: str | None = None,
        domain: str | None = None,
        *,
        caller_label: str = "mcp",
        token: str | None = None,
    ) -> str:
        response = await self._client.request(
            "POST",
            "/api/workflows/recover-article",
            payload={
                "url": url,
                "title": title,
                "domain": domain,
                "caller": caller_label,
            },
            token=token,
        )
        return _workflow_markdown(response)

    async def capture_site(
        self,
        url: str,
        *,
        soft_page_limit: int = 75,
        hard_page_limit: int = 200,
        caller_label: str = "mcp",
        token: str | None = None,
    ) -> str:
        response = await self._client.request(
            "POST",
            "/api/workflows/capture-site",
            payload={
                "url": url,
                "soft_page_limit": soft_page_limit,
                "hard_page_limit": hard_page_limit,
                "caller": caller_label,
            },
            token=token,
        )
        return _workflow_markdown(response)

    async def build_research_pack(
        self,
        topic: str,
        *,
        official_url: str | None = None,
        max_research_pages: int = 40,
        response_format: str = "markdown",
        caller_label: str = "mcp",
        token: str | None = None,
    ) -> str:
        response = await self._client.request(
            "POST",
            "/api/workflows/build-research-pack",
            payload={
                "topic": topic,
                "official_url": official_url,
                "max_research_pages": max_research_pages,
                "caller": caller_label,
            },
            token=token,
        )
        if response_format == "json":
            return json.dumps(response, indent=2)
        return _workflow_markdown(response)
