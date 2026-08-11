"""Legacy tool implementations for explicit standalone MCP development."""

from typing import Any, Callable, Optional

from mcp.server.fastmcp import Context as McpContext

from argus.broker.router import SearchBroker
from argus.corpus import describe_corpus_paths
from argus.models import SearchMode, SearchQuery
from argus.operations.accepted import create_development_accepted_operation_service
from argus.workflows import WorkflowService

_STATUS_DISPLAY = {
    "enabled": "OK",
    "disabled_by_config": "DISABLED (config)",
    "unavailable_missing_key": "MISSING KEY",
    "temporarily_disabled_after_failures": "COOLDOWN",
    "budget_exhausted": "BUDGET EXHAUSTED",
    "degraded": "DEGRADED",
    "healthy": "HEALTHY",
}


def _serialize_response(resp) -> str:
    """Return markdown-formatted search results for LLM consumption."""
    providers_used = [
        t.provider.value for t in resp.traces if t.results_count and t.results_count > 0
    ]
    provider_str = ", ".join(providers_used) if providers_used else "none"
    cached_str = " (cached)" if resp.cached else ""

    lines = [
        f"## Search Results: {resp.query!r}",
        f"Mode: {resp.mode.value} | {resp.total_results} results | via {provider_str}{cached_str}",
        "",
    ]

    for i, r in enumerate(resp.results, 1):
        title = r.title or "(no title)"
        snippet = r.snippet or ""
        egress = r.metadata.get("egress", "unknown")
        lines.append(f"{i}. **{title}**")
        lines.append(f"   URL: {r.url}")
        lines.append(f"   Egress: {egress}")
        if r.score_attribution:
            attribution = ", ".join(
                f"{provider}: {value:.4f}"
                for provider, value in sorted(
                    r.score_attribution.items(),
                    key=lambda item: -item[1],
                )
            )
            lines.append(f"   Score attribution: {attribution}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")

    if resp.budget_warnings:
        lines.append("**Budget warnings:** " + "; ".join(resp.budget_warnings))

    return "\n".join(lines)


async def search_web(
    broker: SearchBroker,
    query: str,
    mode: str = "discovery",
    max_results: int = 10,
    session_id: str = None,
    include_attribution: bool = False,
    free_only: bool = False,
    caller: str = "mcp",
    caller_label: str = "",
) -> str:
    """Search the web using the Argus broker.

    Args:
        query: Search query string
        mode: Search mode (recovery, discovery, grounding, research)
        max_results: Maximum results to return
        session_id: Optional session ID for multi-turn context
        include_attribution: Include per-provider score attribution
        free_only: Only use free search providers
        caller: Caller identifier for attribution (default "mcp")
    """
    search_mode = SearchMode(mode)
    q = SearchQuery(
        query=query,
        mode=search_mode,
        max_results=max_results,
        free_only=free_only,
        caller=caller,
        metadata={"caller_label": caller_label},
    )

    if session_id:
        resp, sid = await broker.search_with_session(
            q,
            session_id=session_id,
            compute_attribution=include_attribution,
        )
        md = _serialize_response(resp)
        return md + f"\n_Session ID: {sid}_"

    resp = await broker.search(q, compute_attribution=include_attribution)
    return _serialize_response(resp)


async def recover_url(
    broker: SearchBroker,
    url: str,
    title: Optional[str] = None,
    domain: Optional[str] = None,
    caller_identity: str = "local-mcp",
    caller_label: str = "",
) -> str:
    """Recover a dead, moved, or unavailable URL.

    Tries search-based recovery first, then archive.ph as fallback.

    Args:
        url: The URL to recover
        title: Optional title hint
        domain: Optional domain hint
    """
    # First try search-based recovery
    query_parts = [url]
    if title:
        query_parts.append(title)
    if domain:
        query_parts.append(domain)

    q = SearchQuery(
        query=" ".join(query_parts),
        mode=SearchMode.RECOVERY,
        max_results=10,
        caller=caller_identity,
        metadata={"caller_label": caller_label},
    )
    resp = await broker.search(q)

    # If search found results, return those
    if resp.results:
        return _serialize_response(resp)

    # Fallback: try archive.ph
    try:
        archive_result = await _try_archive_ph(url)
        if archive_result:
            md = _serialize_response(resp)
            ar = archive_result
            md += f"\n{len(resp.results) + 1}. **{ar.get('title', '(archive)')}** _(archive.ph)_\n   URL: {ar['url']}\n   {ar.get('snippet', '')}\n"
            return md
    except Exception as e:
        return _serialize_response(resp) + f"\n_archive.ph error: {e}_"

    return _serialize_response(resp)


async def _try_archive_ph(url: str) -> Optional[dict]:
    """Try to fetch content from archive.ph."""
    from argus.recovery.archive_ph import try_archive_ph

    result = await try_archive_ph(url)
    if result:
        result["provider"] = "archive_ph"
    return result


async def expand_links(
    broker: SearchBroker,
    query: str,
    context: Optional[str] = None,
    caller_identity: str = "local-mcp",
    caller_label: str = "",
) -> str:
    """Expand a query with related links for discovery.

    Args:
        query: Query to expand
        context: Optional context for better results
    """
    query_text = f"{query} {context}" if context else query
    q = SearchQuery(
        query=query_text,
        mode=SearchMode.DISCOVERY,
        max_results=15,
        caller=caller_identity,
        metadata={"caller_label": caller_label},
    )
    resp = await broker.search(q)
    return _serialize_response(resp)


def search_health(broker: SearchBroker) -> str:
    """Get health status of all search providers.

    Returns provider availability, health state, and any active cooldowns.
    """
    from argus.models import ProviderName

    lines = ["## Search Provider Health", ""]
    lines.append(f"{'Provider':<20} {'Status':<25} {'Failures'}")
    lines.append("-" * 55)

    for pname in ProviderName:
        status = broker.get_provider_status(pname)
        raw = status["effective_status"]
        display = _STATUS_DISPLAY.get(
            raw if isinstance(raw, str) else raw.value, str(raw)
        )
        failures = status.get("consecutive_failures", 0)
        lines.append(f"{pname.value:<20} {display:<25} {failures}")

    return "\n".join(lines)


def search_budgets(broker: SearchBroker) -> str:
    """Get budget status for all providers.

    Returns remaining budget, monthly usage, and exhaustion status.
    """
    from argus.models import ProviderName

    lines = ["## Search Provider Budgets", ""]

    for pname in ProviderName:
        summary = broker.provider_budget_projection(pname)
        snapshot = summary.get("provider_snapshot")
        snapshot_text = "none"
        if snapshot:
            snapshot_text = (
                f"source=provider observed_at={snapshot['observed_at']} "
                f"balance={snapshot['balance']}"
            )
        lines.append(
            f"- **{pname.value}**: remaining={'unlimited' if summary['remaining'] is None else summary['remaining']} "
            f"estimated={summary['argus_estimated_charge']} "
            f"uncertain={summary['uncertain_charge']}; {snapshot_text}"
        )

    return "\n".join(lines)


async def test_provider_mcp(
    broker: SearchBroker,
    provider: str,
    query: str = "argus",
    caller_identity: str = "local-mcp",
    caller_label: str = "",
    live: bool = False,
    idempotency_key: str | None = None,
    durable_receipt: str | None = None,
    spend_reserved: bool = False,
) -> str:
    """Smoke-test a single provider.

    Args:
        provider: Provider name (searxng, brave, serper, tavily, exa)
        query: Test query
    """
    from argus.broker.budgets import PROVIDER_TIERS
    from argus.broker.execution import conservative_charge_estimate
    from argus.broker.readiness import ProbeAuthorization
    from argus.models import ProviderName, SearchQuery

    try:
        pname = ProviderName(provider)
    except ValueError:
        return f"**Error:** Unknown provider: {provider}"

    if not live:
        decision = broker.readiness_service.authorize_probe(pname, "fixture")
        readiness = broker.provider_readiness_projection(pname)
        return (
            f"## Provider Test: {provider}\n"
            f"Mode: fixture\nStatus: "
            f"{'fixture_verified' if decision.allowed else 'denied'}\n"
            f"Readiness: {readiness['state']}"
        )
    probe_query = SearchQuery(
        query=query,
        max_results=3,
        providers=[pname],
        caller=caller_identity,
        user_visible=False,
    )
    authorization = ProbeAuthorization(
        workflow="explicit_validation",
        provider=pname,
        named_quota="free_provider_request" if PROVIDER_TIERS[pname] == 0 else None,
        idempotency_key=idempotency_key,
        durable_receipt=durable_receipt,
        conservative_charge=(
            conservative_charge_estimate(pname, probe_query)
            if PROVIDER_TIERS[pname] > 0
            else None
        ),
    )
    kind = "no_money_quota" if PROVIDER_TIERS[pname] == 0 else "billable_search"
    decision = broker.readiness_service.authorize_probe(pname, kind, authorization)
    if not decision.allowed:
        return f"**Error:** Live probe denied: {decision.reason}"

    q = SearchQuery(
        query=query,
        mode=SearchMode.DISCOVERY,
        max_results=3,
        providers=[pname],
        caller=caller_identity,
        user_visible=False,
        metadata={
            "caller_label": caller_label,
            "probe_receipt": durable_receipt,
            "probe_idempotency_key": idempotency_key,
            "probe_provider": pname.value,
            "probe_no_fallback": True,
            "probe_attempt_id": decision.attempt_id,
        },
    )
    response = await broker.search(q)

    lines = [
        f"## Provider Test: {provider}",
        f"Results: {response.total_results}",
    ]
    for trace in response.traces:
        lines.append(
            f"Status: {trace.status} | {trace.results_count} results | "
            f"{trace.latency_ms}ms"
        )
        if trace.error:
            lines.append(f"Error: {trace.error}")
    for i, r in enumerate(response.results[:3], 1):
        lines.append(
            f"\n{i}. **{r.title}**\n   {r.url}\n   {r.snippet[:100] if r.snippet else ''}"
        )
    return "\n".join(lines)


def _require_standalone_mode(operation: str) -> None:
    from argus.authority import (
        AuthorityConfigurationError,
        adapter_execution_mode,
    )

    if adapter_execution_mode() != "standalone":
        raise AuthorityConfigurationError(
            f"Direct MCP {operation} requires development standalone mode"
        )


async def development_mcp_extract(url: str, *, domain: str | None = None):
    """Run the legacy standalone extractor only behind the development gate."""

    _require_standalone_mode("extraction")
    from argus.extraction import extract_url

    return await extract_url(url, domain=domain, caller="mcp")


async def development_mcp_valyu_answer(
    query: str,
    *,
    fast_mode: bool = False,
):
    """Run the legacy standalone answer provider only behind its dev gate."""

    _require_standalone_mode("provider access")
    from argus.providers.valyu_answer import valyu_answer as provider_answer

    return await provider_answer(query, fast_mode=fast_mode)


def development_mcp_cookie_health() -> dict[str, dict[str, object]]:
    """Read legacy standalone cookie health only behind the development gate."""

    _require_standalone_mode("cookie access")
    from argus.extraction.cookies import get_health_summary

    return get_health_summary()


async def valyu_answer(query: str, fast_mode: bool = False) -> str:
    """Get an AI-synthesized answer grounded in real-time search results.

    Args:
        query: Question or research query to answer
        fast_mode: Use faster mode with lower latency
    """
    result = await development_mcp_valyu_answer(query, fast_mode=fast_mode)

    if result.error:
        return f"**valyu_answer error:** {result.error}"

    lines = [result.answer or "(no answer)", ""]
    if result.sources:
        lines.append("**Sources:**")
        for s in result.sources[:10]:
            title = s.get("title", "")
            url = s.get("url", "")
            if title and url:
                lines.append(f"- [{title}]({url})")
            elif url:
                lines.append(f"- {url}")

    return "\n".join(lines)


def argus_paths() -> str:
    """Show the resolved Argus runtime storage layout."""
    paths = describe_corpus_paths()
    lines = ["## Argus Storage Paths", ""]
    for k, v in paths.items():
        lines.append(f"- **{k}**: {v}")
    return "\n".join(lines)


async def extract_content(url: str, domain: str = None) -> str:
    """Extract clean text content from a URL.

    Args:
        url: URL to extract content from
        domain: Optional domain hint for authenticated extraction (e.g. nytimes.com)
    """
    result = await development_mcp_extract(url, domain=domain)

    if result.error:
        return f"**Extraction failed:** {result.error}\nURL: {result.url}"

    meta_parts = []
    if result.author:
        meta_parts.append(f"Author: {result.author}")
    if result.date:
        meta_parts.append(f"Date: {result.date}")
    if result.word_count:
        meta_parts.append(f"Words: {result.word_count}")
    if result.extractor:
        meta_parts.append(f"Extractor: {result.extractor.value}")
    if result.egress:
        meta_parts.append(f"Egress: {result.egress}")
    if result.machine:
        meta_parts.append(f"Machine: {result.machine}")

    lines = [
        f"# {result.title or result.url}",
        f"URL: {result.url}",
    ]
    if meta_parts:
        lines.append(" | ".join(meta_parts))
    lines.append("")
    if result.text:
        lines.append(result.text)

    return "\n".join(lines)


def _serialize_workflow_json(result) -> str:
    """Machine-readable workflow result: run metadata plus a file manifest.

    Designed so an MCP agent can enumerate pack files and fetch each one via
    read_pack_file, then forward them (e.g. to Maya POST /ingest/file)
    without shelling out.
    """
    import os
    import json

    files = []
    seen: set[str] = set()
    candidate_paths = []
    if result.artifacts:
        candidate_paths.extend([a.path for a in result.artifacts])
    if result.documents:
        candidate_paths.extend([d.artifact_path for d in result.documents])
    for path in candidate_paths:
        if not path or path in seen:
            continue
        seen.add(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        files.append({"path": path, "bytes": size})

    payload = {
        "run_id": result.run_id,
        "kind": result.kind.value,
        "status": result.status.value,
        "target": result.target,
        "error": result.error,
        "report_path": result.report_path,
        "manifest_path": result.manifest_path,
        "snapshot_dir": result.snapshot_dir,
        "pack_dir": result.metadata.get("current_dir") if result.metadata else None,
        "files": files,
    }
    return json.dumps(payload, indent=2)


def _serialize_workflow(result) -> str:
    if result.error:
        return f"**Workflow error ({result.kind.value}):** {result.error}\nTarget: {result.target}"

    lines = [
        f"## {result.kind.value.replace('_', ' ').title()}: {result.target}",
        f"Status: {result.status.value} | Run: {result.run_id}",
        "",
    ]

    if result.summary_sections:
        for section in result.summary_sections:
            lines.append(f"### {section.heading}")
            lines.append(section.body)
            lines.append("")
    elif result.report_path:
        try:
            import os

            if os.path.isfile(result.report_path):
                with open(result.report_path) as f:
                    lines.append(f.read())
            else:
                lines.append(f"_Report saved to: {result.report_path}_")
        except Exception:
            lines.append(f"_Report saved to: {result.report_path}_")

    if result.documents:
        lines.append(f"\n**{len(result.documents)} documents processed**")
        for doc in result.documents[:5]:
            lines.append(f"- {doc.title or doc.url}")
        if len(result.documents) > 5:
            lines.append(f"- ... and {len(result.documents) - 5} more")

    if result.snapshot_dir:
        lines.append(f"\n_Artifacts saved to: {result.snapshot_dir}_")

    return "\n".join(lines)


def _make_progress_callback(ctx: Any) -> Callable[[int, int, str], None] | None:
    if ctx is None:
        return None

    def cb(current: int, total: int, message: str) -> None:
        try:
            ctx.report_progress(current, total, message)
        except Exception:
            pass

    return cb


async def recover_dead_article(
    broker: SearchBroker,
    url: str,
    title: Optional[str] = None,
    domain: Optional[str] = None,
    ctx: Any = None,
    caller_identity: str = "local-mcp",
    caller_label: str = "",
) -> str:
    """Recover a dead article into a local citation-backed report."""
    service = WorkflowService(
        create_development_accepted_operation_service(broker),
        progress_callback=_make_progress_callback(ctx),
        caller=caller_identity,
    )
    result = await service.recover_article(
        url=url,
        title=title,
        domain=domain,
        caller_identity=caller_identity,
        caller_label=caller_label,
    )
    return _serialize_workflow(result)


async def capture_site(
    broker: SearchBroker,
    url: str,
    soft_page_limit: int = 75,
    hard_page_limit: int = 200,
    ctx: Any = None,
    caller_identity: str = "local-mcp",
    caller_label: str = "",
) -> str:
    """Capture the important pages from a site and summarize them."""
    service = WorkflowService(
        create_development_accepted_operation_service(broker),
        progress_callback=_make_progress_callback(ctx),
        caller=caller_identity,
    )
    result = await service.capture_site(
        url=url,
        soft_page_limit=soft_page_limit,
        hard_page_limit=hard_page_limit,
        caller_identity=caller_identity,
        caller_label=caller_label,
    )
    return _serialize_workflow(result)


async def build_research_pack(
    broker: SearchBroker,
    topic: str,
    official_url: Optional[str] = None,
    max_research_pages: int = 40,
    research_targets: list[dict[str, Any]] | None = None,
    free_only: bool = False,
    response_format: str = "markdown",
    ctx: Any = None,
    caller_identity: str = "local-mcp",
    caller_label: str = "",
) -> str:
    """Build a combined official-docs and external-research pack."""
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
    payload = request.model_dump(mode="json")
    service = WorkflowService(
        create_development_accepted_operation_service(broker),
        progress_callback=_make_progress_callback(ctx),
        caller=caller_identity,
    )
    result = await service.build_research_pack(
        topic=payload["topic"],
        official_url=payload["official_url"],
        max_research_pages=payload["max_research_pages"],
        research_targets=payload["research_targets"],
        free_only=payload["free_only"],
        caller_identity=caller_identity,
        caller_label=payload["caller"],
    )
    if response_format == "json":
        return _serialize_workflow_json(result)
    return _serialize_workflow(result)


def cookie_health() -> str:
    """Get health status of all configured cookie domains.

    Returns per-domain status, request counts, staleness warnings,
    and whether cookies need refreshing.
    """
    summary = development_mcp_cookie_health()
    refresh = [d for d, s in summary.items() if s.get("stale_warning")]

    lines = [f"## Cookie Health ({len(summary)} domains)", ""]
    for domain, s in summary.items():
        status = s.get("status", "unknown")
        reqs = s.get("request_count", 0)
        flag = " ⚠ stale" if s.get("stale_warning") else ""
        lines.append(f"- **{domain}**: {status}, {reqs} requests{flag}")

    if refresh:
        lines.append(f"\n**Needs refresh:** {', '.join(refresh)}")
    return "\n".join(lines)


def read_pack_file(path: str, max_bytes: int = 262144, offset: int = 0) -> str:
    """Read a workflow artifact file from inside the Argus data root.

    Returns JSON: {path, size, offset, bytes_returned, truncated,
    encoding: "utf-8"|"base64", content}. Rejects paths outside the
    Argus data root (research packs, docs cache, snapshots all live there).
    """
    import base64
    import json
    from pathlib import Path

    from argus.corpus.paths import resolve_data_root

    root = Path(resolve_data_root()).resolve()
    target = Path(path).resolve()
    if not (target == root or target.is_relative_to(root)):
        return json.dumps(
            {"error": "path is outside the Argus data root", "data_root": str(root)}
        )
    if not target.is_file():
        return json.dumps({"error": "file not found", "path": str(target)})

    size = target.stat().st_size
    max_bytes = max(1, min(max_bytes, 1_048_576))
    offset = max(0, offset)
    with open(target, "rb") as handle:
        handle.seek(offset)
        chunk = handle.read(max_bytes)

    try:
        content = chunk.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = base64.b64encode(chunk).decode("ascii")
        encoding = "base64"

    return json.dumps(
        {
            "path": str(target),
            "size": size,
            "offset": offset,
            "bytes_returned": len(chunk),
            "truncated": offset + len(chunk) < size,
            "encoding": encoding,
            "content": content,
        }
    )


def register_standalone_tools(
    mcp,
    backend,
    *,
    caller_identity: Callable[[], str],
) -> None:
    """Register development-only tools without granting authority to MCP modules."""
    from argus.api.schemas import ResearchTarget
    from argus import development_mcp_resources as resources

    @mcp.tool()
    async def valyu_answer(query: str, fast_mode: bool = False) -> str:
        return await globals()["valyu_answer"](query, fast_mode=fast_mode)

    @mcp.tool()
    def argus_paths() -> str:
        return globals()["argus_paths"]()

    @mcp.tool()
    async def recover_dead_article(
        url: str,
        title: str = None,
        domain: str = None,
        caller: str = "mcp",
        ctx: McpContext = None,
    ) -> str:
        return await globals()["recover_dead_article"](
            backend.broker,
            url,
            title,
            domain,
            ctx=ctx,
            caller_identity=caller_identity(),
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
        return await globals()["capture_site"](
            backend.broker,
            url,
            soft_page_limit=soft_page_limit,
            hard_page_limit=hard_page_limit,
            ctx=ctx,
            caller_identity=caller_identity(),
            caller_label=caller,
        )

    @mcp.tool()
    async def build_research_pack(
        topic: str,
        official_url: str = None,
        max_research_pages: int = 40,
        research_targets: list[ResearchTarget] = None,
        free_only: bool = False,
        response_format: str = "markdown",
        caller: str = "mcp",
        ctx: McpContext = None,
    ) -> str:
        return await globals()["build_research_pack"](
            backend.broker,
            topic,
            official_url=official_url,
            max_research_pages=max_research_pages,
            research_targets=(
                [target.model_dump(mode="json") for target in research_targets]
                if research_targets
                else None
            ),
            free_only=free_only,
            response_format=response_format,
            ctx=ctx,
            caller_identity=caller_identity(),
            caller_label=caller,
        )

    @mcp.tool()
    def read_pack_file(
        path: str,
        max_bytes: int = 262144,
        offset: int = 0,
    ) -> str:
        return globals()["read_pack_file"](
            path,
            max_bytes=max_bytes,
            offset=offset,
        )

    @mcp.tool()
    async def test_provider(
        provider: str,
        query: str = "argus",
        live: bool = False,
        idempotency_key: str | None = None,
        durable_receipt: str | None = None,
        spend_reserved: bool = False,
    ) -> str:
        return await test_provider_mcp(
            backend.broker,
            provider,
            query,
            caller_identity=caller_identity(),
            caller_label="mcp-admin-smoke",
            live=live,
            idempotency_key=idempotency_key,
            durable_receipt=durable_receipt,
            spend_reserved=spend_reserved,
        )

    @mcp.tool()
    def cookie_health() -> str:
        return globals()["cookie_health"]()

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
