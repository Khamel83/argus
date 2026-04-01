"""
MCP tool definitions for Argus.
"""

import json
from typing import Optional

from argus.broker.router import SearchBroker
from argus.models import SearchMode, SearchQuery


def _serialize_response(resp) -> str:
    results = []
    for r in resp.results:
        results.append({
            "url": r.url,
            "title": r.title,
            "snippet": r.snippet,
            "provider": r.provider.value if r.provider else None,
            "score": round(r.score, 4),
        })

    traces = []
    for t in resp.traces:
        traces.append({
            "provider": t.provider.value,
            "status": t.status,
            "results_count": t.results_count,
            "latency_ms": t.latency_ms,
            "error": t.error,
        })

    return json.dumps({
        "query": resp.query,
        "mode": resp.mode.value,
        "results": results,
        "total_results": resp.total_results,
        "cached": resp.cached,
        "traces": traces,
        "run_id": resp.search_run_id,
    }, indent=2)


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
        result = json.loads(_serialize_response(resp))
        result["session_id"] = sid
        return json.dumps(result, indent=2)

    resp = await broker.search(q)
    return _serialize_response(resp)


async def recover_url(
    broker: SearchBroker,
    url: str,
    title: Optional[str] = None,
    domain: Optional[str] = None,
) -> str:
    """Recover a dead, moved, or unavailable URL.

    Args:
        url: The URL to recover
        title: Optional title hint
        domain: Optional domain hint
    """
    query_parts = [url]
    if title:
        query_parts.append(title)
    if domain:
        query_parts.append(domain)

    q = SearchQuery(query=" ".join(query_parts), mode=SearchMode.RECOVERY, max_results=10)
    resp = await broker.search(q)
    return _serialize_response(resp)


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

    providers = {}
    for pname in ProviderName:
        providers[pname.value] = broker.get_provider_status(pname)

    return json.dumps({
        "providers": providers,
        "health_tracking": broker.health_tracker.get_all_status(),
    }, indent=2)


def search_budgets(broker: SearchBroker) -> str:
    """Get budget status for all providers.

    Returns remaining budget, monthly usage, and exhaustion status.
    """
    from argus.models import ProviderName

    budgets = {}
    for pname in ProviderName:
        budgets[pname.value] = {
            "remaining": broker.budget_tracker.get_remaining_budget(pname),
            "monthly_usage": broker.budget_tracker.get_monthly_usage(pname),
            "usage_count": broker.budget_tracker.get_usage_count(pname),
            "exhausted": broker.budget_tracker.is_budget_exhausted(pname),
        }

    return json.dumps({"budgets": budgets}, indent=2)


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
        return json.dumps({"error": f"Unknown provider: {provider}"})

    prov = broker._providers.get(pname)
    if prov is None:
        return json.dumps({"error": f"Provider not registered: {provider}"})

    q = SearchQuery(query=query, mode=SearchMode.DISCOVERY, max_results=3)
    results, trace = await prov.search(q)

    return json.dumps({
        "provider": provider,
        "available": prov.is_available(),
        "status": prov.status().value,
        "trace": {
            "status": trace.status,
            "results_count": trace.results_count,
            "latency_ms": trace.latency_ms,
            "error": trace.error,
        },
        "sample_results": [
            {"url": r.url, "title": r.title, "snippet": r.snippet[:100]}
            for r in results[:3]
        ],
    }, indent=2)


async def extract_content(url: str) -> str:
    """Extract clean text content from a URL.

    Args:
        url: URL to extract content from
    """
    from argus.extraction import extract_url

    result = await extract_url(url)

    return json.dumps({
        "url": result.url,
        "title": result.title,
        "text": result.text,
        "author": result.author,
        "date": result.date,
        "word_count": result.word_count,
        "extractor": result.extractor.value if result.extractor else None,
        "error": result.error,
    }, indent=2)
