"""Stateless MCP presentation adapter over the authenticated HTTP authority."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any
from urllib.parse import quote

from argus.authority import HttpAuthorityClient
from argus.operations.presentation import (
    budget_remaining,
    nested_status_failures,
    provider_display_state,
)


def _adapter_unready(detail: str) -> dict[str, Any]:
    request_id = f"mcp-{secrets.token_hex(8)}"
    return {
        "contract_version": "2.0",
        "outcome": "unready",
        "request_id": request_id,
        "result": None,
        "error": {
            "type": "urn:argus:problem:unready",
            "title": "Unready",
            "status": 503,
            "detail": detail,
            "instance": f"urn:argus:request:{request_id}",
            "code": "unready",
            "retryable": False,
            "retry_after_seconds": None,
        },
    }


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


def _workflow_start_json(payload: dict[str, Any]) -> dict[str, Any]:
    """Render only safe start metadata from an authority response."""
    run_id = str(payload.get("run_id", ""))
    if "request_sha256" in payload:
        return {
            "run_id": run_id,
            "kind": payload.get("kind", "workflow"),
            "status": payload.get("status", "unknown"),
            "target": payload.get("target", ""),
            "created_at": payload.get("created_at"),
            "status_url": payload.get(
                "status_url",
                f"/api/workflows/{quote(run_id, safe='')}/status",
            ),
            "request_sha256": payload.get("request_sha256", ""),
        }
    return {
        "run_id": run_id,
        "kind": payload.get("kind", "workflow"),
        "status": payload.get("status", "unknown"),
        "target": payload.get("target", ""),
        "created_at": payload.get("created_at"),
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "status_url": f"/api/workflows/{quote(run_id, safe='')}/status",
    }


def _build_research_pack_payload(
    *,
    topic: str,
    official_url: str | None,
    max_research_pages: int,
    research_targets: list[dict[str, Any]] | None,
    free_only: bool,
    caller_label: str,
) -> dict[str, Any]:
    """Validate and JSON-project one build request before HTTP transport."""

    from argus.api.schemas import BuildResearchPackWorkflowRequest

    request = BuildResearchPackWorkflowRequest.model_validate(
        {
            "topic": topic,
            "official_url": official_url,
            "max_research_pages": max_research_pages,
            "research_targets": research_targets or [],
            "free_only": free_only,
            "caller": caller_label,
        }
    )
    return request.model_dump(mode="json")


def _workflow_status_markdown(payload: dict[str, Any]) -> str:
    """Render only the safe workflow status fields supplied by the authority."""
    lines = [
        f"## {str(payload.get('kind', 'workflow')).replace('_', ' ').title()}",
        f"Status: {payload.get('status', 'unknown')} | Run: {payload.get('run_id', '')}",
        f"Target: {payload.get('target', '')}",
        (
            f"Sources: {payload.get('source_count', 0)} | "
            f"Domains: {payload.get('domain_count', 0)} | "
            f"Primary: {payload.get('primary_source_count', 0)}"
        ),
        f"Cost: {payload.get('cost_state', 'unavailable')}",
    ]
    runtime = payload.get("runtime") or {}
    if runtime:
        lines.append(
            "Runtime: "
            f"{runtime.get('version', 'unknown')} | "
            f"revision={runtime.get('source_revision', 'unknown')} | "
            f"image={runtime.get('image_identity', 'unknown')} | "
            f"deployment={runtime.get('deployment_identity', 'unknown')}"
        )
    artifacts = payload.get("artifacts") or []
    if artifacts:
        lines.extend(
            [
                "",
                "### Artifacts",
                "",
            ]
        )
        for artifact in artifacts:
            availability = "available" if artifact.get("available") else "unavailable"
            size = artifact.get("size_bytes")
            lines.append(
                f"- {artifact.get('kind', 'artifact')}: {availability}"
                + (f", {size} bytes" if size is not None else "")
                + (f", sha256={artifact['sha256']}" if artifact.get("sha256") else "")
            )
    reasons = [
        *(payload.get("partial_reasons") or []),
        *(payload.get("degraded_reasons") or []),
    ]
    if reasons:
        lines.extend(["", "Reasons: " + "; ".join(str(reason) for reason in reasons)])
    return "\n".join(lines)


def _workflow_status_json(payload: dict[str, Any]) -> str:
    """Serialize the path-free status schema and discard authority extras."""

    from argus.api.schemas import WorkflowStatusResponse

    safe = WorkflowStatusResponse.model_validate(payload)
    return json.dumps(safe.model_dump(mode="json"), indent=2)


def _workflow_artifact_markdown(payload: dict[str, Any]) -> str:
    """Render bounded artifact content with its bounded-read metadata."""
    lines = [
        f"## Workflow Artifact: {payload.get('artifact', payload.get('kind', 'unknown'))}",
        f"Run: {payload.get('run_id', '')}",
        (
            f"Bytes: {payload.get('bytes_returned', 0)}/"
            f"{payload.get('total_bytes', 0)} | "
            f"offset={payload.get('offset', 0)} | "
            f"truncated={payload.get('truncated', False)}"
        ),
        f"SHA-256: {payload.get('sha256', '')}",
        "",
        "### Content",
        "",
        str(payload.get("content", "")),
    ]
    if payload.get("next_offset") is not None:
        lines.insert(4, f"Next offset: {payload['next_offset']}")
    return "\n".join(lines)


def _workflow_artifact_json(payload: dict[str, Any]) -> str:
    """Serialize the bounded artifact schema and discard authority extras."""

    from argus.api.schemas import WorkflowArtifactReadResponse

    safe = WorkflowArtifactReadResponse.model_validate(payload)
    return json.dumps(safe.model_dump(mode="json"), indent=2)


class HttpMcpAdapter:
    """Translate typed MCP calls without owning execution resources."""

    def __init__(self, client: HttpAuthorityClient):
        self._client = client

    async def _v2_request(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        token: str | None,
    ) -> dict[str, Any]:
        from argus.authority import AuthorityRequestError

        selection = await self._client.resolve_http_contract(
            None,
            time.monotonic,
        )
        if (
            selection.outcome != "ready"
            or selection.contract_version != "2.0"
            or selection.base_path != "/api/v2"
        ):
            return _adapter_unready("Argus HTTP contract discovery is unavailable")
        try:
            return await self._client.request_v2(
                path,
                payload=payload,
                token=token,
            )
        except AuthorityRequestError:
            return _adapter_unready("Argus HTTP execution authority is unavailable")

    async def search_web_v2(
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
    ) -> dict[str, Any]:
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
        return await self._v2_request(
            "/api/v2/search",
            payload=request,
            token=token,
        )

    async def recover_url_v2(
        self,
        url: str,
        title: str | None = None,
        domain: str | None = None,
        *,
        caller_label: str = "mcp",
        caller_identity: str = "mcp",
        token: str | None = None,
    ) -> dict[str, Any]:
        del caller_label, caller_identity
        return await self._v2_request(
            "/api/v2/recover-url",
            payload={"url": url, "title": title, "domain": domain},
            token=token,
        )

    async def expand_links_v2(
        self,
        query: str,
        context: str | None = None,
        *,
        caller_label: str = "mcp",
        caller_identity: str = "mcp",
        token: str | None = None,
    ) -> dict[str, Any]:
        del caller_label, caller_identity
        return await self._v2_request(
            "/api/v2/expand",
            payload={"query": query, "context": context},
            token=token,
        )

    async def extract_content_v2(
        self,
        url: str,
        domain: str | None = None,
        *,
        caller_label: str = "mcp",
        caller_identity: str = "mcp",
        token: str | None = None,
    ) -> dict[str, Any]:
        del caller_identity
        return await self._v2_request(
            "/api/v2/extract",
            payload={"url": url, "domain": domain, "caller": caller_label},
            token=token,
        )

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
            lines.append(f"- **{provider}**: {provider_display_state(status)}")
            if not status.get("state") and status.get("effective_status"):
                lines.append(
                    f"  - legacy_effective_status={status['effective_status']}"
                )
            lines.extend(f"  - {failure}" for failure in nested_status_failures(status))
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
                f"- **{provider}**: remaining={budget_remaining(summary.get('remaining'))} "
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
        research_targets: list[dict[str, Any]] | None = None,
        free_only: bool = False,
        response_format: str = "markdown",
        caller_label: str = "mcp",
        caller_identity: str = "mcp",
        token: str | None = None,
    ) -> str:
        del caller_identity
        payload = _build_research_pack_payload(
            topic=topic,
            official_url=official_url,
            max_research_pages=max_research_pages,
            research_targets=research_targets,
            free_only=free_only,
            caller_label=caller_label,
        )
        response = await self._client.request(
            "POST",
            "/api/workflows/build-research-pack/start",
            payload=payload,
            token=token,
        )
        if response_format == "json":
            return json.dumps(_workflow_start_json(response), indent=2)
        return _workflow_markdown(response)

    async def get_workflow_status(
        self,
        run_id: str,
        *,
        response_format: str = "markdown",
        token: str | None = None,
    ) -> str:
        """Read the safe workflow projection from the HTTP authority."""
        path = f"/api/workflows/{quote(str(run_id), safe='')}/status"
        response = await self._client.request("GET", path, token=token)
        if response_format == "json":
            return _workflow_status_json(response)
        return _workflow_status_markdown(response)

    async def read_workflow_artifact(
        self,
        run_id: str,
        artifact: str = "report",
        *,
        offset: int = 0,
        max_bytes: int = 65536,
        response_format: str = "markdown",
        token: str | None = None,
    ) -> str:
        """Read one bounded report/manifest slice through the HTTP authority."""
        if artifact not in {"report", "manifest"}:
            raise ValueError("artifact must be report or manifest")
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
        ):
            raise ValueError("offset and max_bytes must be integers")
        safe_offset = offset
        safe_max_bytes = max_bytes
        if safe_offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= safe_max_bytes <= 256 * 1024:
            raise ValueError("max_bytes must be between 1 and 262144")
        path = (
            f"/api/workflows/{quote(str(run_id), safe='')}/artifacts/"
            f"{quote(artifact, safe='')}?offset={safe_offset}&max_bytes={safe_max_bytes}"
        )
        response = await self._client.request("GET", path, token=token)
        if response_format == "json":
            return _workflow_artifact_json(response)
        return _workflow_artifact_markdown(response)
