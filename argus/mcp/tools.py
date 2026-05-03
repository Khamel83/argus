"""
MCP tool definitions for Argus.
"""

from typing import Any, Callable, Optional

from argus.broker.router import SearchBroker
from argus.corpus import describe_corpus_paths
from argus.models import SearchMode, SearchQuery
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
    providers_used = [t.provider.value for t in resp.traces if t.results_count and t.results_count > 0]
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
) -> str:
    """Search the web using the Argus broker.

    Args:
        query: Search query string
        mode: Search mode (recovery, discovery, grounding, research)
        max_results: Maximum results to return
        session_id: Optional session ID for multi-turn context
    """
    search_mode = SearchMode(mode)
    q = SearchQuery(query=query, mode=search_mode, max_results=max_results)

    if session_id:
        resp, sid = await broker.search_with_session(q, session_id=session_id)
        md = _serialize_response(resp)
        return md + f"\n_Session ID: {sid}_"

    resp = await broker.search(q)
    return _serialize_response(resp)


async def recover_url(
    broker: SearchBroker,
    url: str,
    title: Optional[str] = None,
    domain: Optional[str] = None,
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

    q = SearchQuery(query=" ".join(query_parts), mode=SearchMode.RECOVERY, max_results=10)
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
    import httpx

    from urllib.parse import quote_plus

    archive_url = f"https://archive.ph/newest/{quote_plus(url)}"

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(archive_url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })

        if resp.status_code != 200:
            return None

        html = resp.text

        # Check if archive.ph actually has this page
        if "does not have an archive" in html or "was not archived" in html:
            return None

        # Extract text from archived page
        import trafilatura
        loop = __import__("asyncio").get_event_loop()
        extracted = await loop.run_in_executor(None, trafilatura.bare_extraction, html)

        if extracted and extracted.get("text") and len(extracted["text"]) > 200:
            return {
                "url": str(resp.url),
                "title": extracted.get("title", ""),
                "snippet": extracted["text"][:200],
                "domain": "archive.ph",
                "provider": "archive_ph",
                "score": 0.8,
            }

    return None


async def expand_links(
    broker: SearchBroker,
    query: str,
    context: Optional[str] = None,
) -> str:
    """Expand a query with related links for discovery.

    Args:
        query: Query to expand
        context: Optional context for better results
    """
    query_text = f"{query} {context}" if context else query
    q = SearchQuery(query=query_text, mode=SearchMode.DISCOVERY, max_results=15)
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
        display = _STATUS_DISPLAY.get(raw if isinstance(raw, str) else raw.value, str(raw))
        failures = status.get("consecutive_failures", 0)
        lines.append(f"{pname.value:<20} {display:<25} {failures}")

    return "\n".join(lines)


def search_budgets(broker: SearchBroker) -> str:
    """Get budget status for all providers.

    Returns remaining budget, monthly usage, and exhaustion status.
    """
    from argus.models import ProviderName

    lines = ["## Search Provider Budgets", ""]
    lines.append(f"{'Provider':<20} {'Remaining':<12} {'Used/Month':<12} {'Total Used':<12} {'Status'}")
    lines.append("-" * 70)

    for pname in ProviderName:
        remaining = broker.budget_tracker.get_remaining_budget(pname)
        monthly = broker.budget_tracker.get_monthly_usage(pname)
        total = broker.budget_tracker.get_usage_count(pname)
        exhausted = broker.budget_tracker.is_budget_exhausted(pname)
        status = "EXHAUSTED" if exhausted else ("unlimited" if remaining == 0 else "ok")
        rem_str = "unlimited" if remaining == 0 else str(remaining)
        lines.append(f"{pname.value:<20} {rem_str:<12} {monthly:<12} {total:<12} {status}")

    return "\n".join(lines)


async def test_provider_mcp(
    broker: SearchBroker,
    provider: str,
    query: str = "argus",
) -> str:
    """Smoke-test a single provider.

    Args:
        provider: Provider name (searxng, brave, serper, tavily, exa)
        query: Test query
    """
    from argus.models import ProviderName, SearchQuery

    try:
        pname = ProviderName(provider)
    except ValueError:
        return f"**Error:** Unknown provider: {provider}"

    prov = broker._providers.get(pname)
    if prov is None:
        return f"**Error:** Provider not registered: {provider}"

    if not prov.is_available():
        return f"**{provider}** — UNAVAILABLE (status: {prov.status().value})"

    q = SearchQuery(query=query, mode=SearchMode.DISCOVERY, max_results=3)
    results, trace = await prov.search(q)

    lines = [
        f"## Provider Test: {provider}",
        f"Status: {prov.status().value} | {trace.status} | {trace.results_count} results | {trace.latency_ms}ms",
    ]
    if trace.error:
        lines.append(f"Error: {trace.error}")
    for i, r in enumerate(results[:3], 1):
        lines.append(f"\n{i}. **{r.title}**\n   {r.url}\n   {r.snippet[:100] if r.snippet else ''}")
    return "\n".join(lines)


async def valyu_answer(query: str, fast_mode: bool = False) -> str:
    """Get an AI-synthesized answer grounded in real-time search results.

    Args:
        query: Question or research query to answer
        fast_mode: Use faster mode with lower latency
    """
    from argus.providers.valyu_answer import valyu_answer as _answer

    result = await _answer(query, fast_mode=fast_mode)

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
    from argus.extraction import extract_url

    result = await extract_url(url, domain=domain)

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
) -> str:
    """Recover a dead article into a local citation-backed report."""
    result = await WorkflowService(broker, progress_callback=_make_progress_callback(ctx)).recover_article(
        url=url, title=title, domain=domain,
    )
    return _serialize_workflow(result)


async def capture_site(
    broker: SearchBroker,
    url: str,
    soft_page_limit: int = 75,
    hard_page_limit: int = 200,
    ctx: Any = None,
) -> str:
    """Capture the important pages from a site and summarize them."""
    result = await WorkflowService(broker, progress_callback=_make_progress_callback(ctx)).capture_site(
        url=url,
        soft_page_limit=soft_page_limit,
        hard_page_limit=hard_page_limit,
    )
    return _serialize_workflow(result)


async def build_research_pack(
    broker: SearchBroker,
    topic: str,
    official_url: Optional[str] = None,
    max_research_pages: int = 40,
    ctx: Any = None,
) -> str:
    """Build a combined official-docs and external-research pack."""
    result = await WorkflowService(broker, progress_callback=_make_progress_callback(ctx)).build_research_pack(
        topic=topic,
        official_url=official_url,
        max_research_pages=max_research_pages,
    )
    return _serialize_workflow(result)


def cookie_health() -> str:
    """Get health status of all configured cookie domains.

    Returns per-domain status, request counts, staleness warnings,
    and whether cookies need refreshing.
    """
    from argus.extraction.cookies import get_health_summary

    summary = get_health_summary()
    stale = [d for d, s in summary.items() if s["status"] == "stale"]
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
