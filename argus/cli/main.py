"""
Argus CLI — command-line interface to the search broker.
"""

import asyncio
import json
import sys

import click

from argus.logging import get_logger

logger = get_logger("cli")


def _run(coro):
    """Run an async coroutine from sync context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


@click.group()
@click.version_option(package_name="argus")
def cli():
    """Argus — standalone search broker."""
    pass


@cli.command()
@click.option("--query", "-q", required=True, help="Search query")
@click.option("--mode", "-m", default="discovery", type=click.Choice(["recovery", "discovery", "grounding", "research"]))
@click.option("--max-results", "-n", default=10, help="Max results")
@click.option("--providers", "-p", multiple=False, help="Override providers (comma-separated)")
@click.option("--session", "-s", default=None, help="Session ID for multi-turn context")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def search(query, mode, max_results, providers, as_json, session):
    """Execute a search query."""
    from argus.broker.router import create_broker
    from argus.models import SearchMode, SearchQuery

    broker = create_broker()
    q = SearchQuery(query=query, mode=SearchMode(mode), max_results=max_results)

    if session:
        resp, sid = _run(broker.search_with_session(q, session_id=session))
        session_id = sid
    else:
        resp = _run(broker.search(q))
        session_id = None

    if as_json:
        data = {
            "query": resp.query,
            "mode": resp.mode.value,
            "results": [
                {"url": r.url, "title": r.title, "snippet": r.snippet, "provider": r.provider.value if r.provider else None, "score": r.score}
                for r in resp.results
            ],
            "total_results": resp.total_results,
            "cached": resp.cached,
            "run_id": resp.search_run_id,
        }
        if session_id:
            data["session_id"] = session_id
        click.echo(json.dumps(data, indent=2))
    else:
        click.echo(f"Query: {resp.query}")
        click.echo(f"Mode: {resp.mode.value} | Results: {resp.total_results} | Cached: {resp.cached}")
        click.echo(f"Run ID: {resp.search_run_id}")
        if session_id:
            click.echo(f"Session: {session_id}")
        click.echo()
        for i, r in enumerate(resp.results, 1):
            provider = f" [{r.provider.value}]" if r.provider else ""
            click.echo(f"  {i}. {r.title}{provider}")
            click.echo(f"     {r.url}")
            if r.snippet:
                click.echo(f"     {r.snippet[:120]}")
            click.echo()

        if resp.traces:
            click.echo("Provider traces:")
            for t in resp.traces:
                click.echo(f"  {t.provider.value}: {t.status} ({t.results_count} results, {t.latency_ms}ms)")
                if t.error:
                    click.echo(f"    Error: {t.error}")


@cli.command()
@click.option("--url", "-u", required=True, help="URL to extract content from")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def extract(url, as_json):
    """Extract clean text content from a URL."""
    from argus.extraction import extract_url

    result = _run(extract_url(url))

    if result.error:
        click.echo(f"Error: {result.error}", err=True)
        sys.exit(1)

    if as_json:
        data = {
            "url": result.url,
            "title": result.title,
            "text": result.text,
            "author": result.author,
            "date": result.date,
            "word_count": result.word_count,
            "extractor": result.extractor.value if result.extractor else None,
        }
        click.echo(json.dumps(data, indent=2))
    else:
        if result.title:
            click.echo(f"Title: {result.title}")
        if result.author:
            click.echo(f"Author: {result.author}")
        if result.date:
            click.echo(f"Date: {result.date}")
        click.echo(f"Words: {result.word_count} | Extractor: {result.extractor.value if result.extractor else 'unknown'}")
        click.echo()
        click.echo(result.text)


@cli.command(name="recover-url")
@click.option("--url", "-u", required=True, help="URL to recover")
@click.option("--title", "-t", help="Optional title hint")
@click.option("--domain", "-d", help="Optional domain hint")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def recover_url(url, title, domain, as_json):
    """Recover a dead or moved URL."""
    from argus.broker.router import create_broker
    from argus.models import SearchMode, SearchQuery

    broker = create_broker()
    query_parts = [url]
    if title:
        query_parts.append(title)
    if domain:
        query_parts.append(domain)

    q = SearchQuery(query=" ".join(query_parts), mode=SearchMode.RECOVERY, max_results=10)
    resp = _run(broker.search(q))

    if as_json:
        data = {
            "url": url,
            "results": [
                {"url": r.url, "title": r.title, "snippet": r.snippet}
                for r in resp.results
            ],
        }
        click.echo(json.dumps(data, indent=2))
    else:
        click.echo(f"Recovery for: {url}")
        click.echo(f"Results: {resp.total_results}")
        for i, r in enumerate(resp.results, 1):
            click.echo(f"  {i}. {r.title}")
            click.echo(f"     {r.url}")


@cli.command()
def health():
    """Show provider health status."""
    from argus.broker.router import create_broker
    from argus.models import ProviderName

    broker = create_broker()
    for pname in ProviderName:
        status = broker.get_provider_status(pname)
        effective = status["effective_status"]
        click.echo(f"  {pname.value:12s} {effective if isinstance(effective, str) else effective.value}")
    click.echo()
    all_health = broker.health_tracker.get_all_status()
    if all_health:
        click.echo("Health tracking:")
        for pname, info in all_health.items():
            click.echo(f"  {pname.value}: failures={info['consecutive_failures']} cooldown={info['in_cooldown']}")


@cli.command()
def budgets():
    """Show provider budget status."""
    from argus.broker.router import create_broker
    from argus.models import ProviderName

    broker = create_broker()
    click.echo("Provider budgets:")
    for pname in ProviderName:
        remaining = broker.budget_tracker.get_remaining_budget(pname)
        usage = broker.budget_tracker.get_monthly_usage(pname)
        count = broker.budget_tracker.get_usage_count(pname)
        exhausted = broker.budget_tracker.is_budget_exhausted(pname)

        budget_str = f"${remaining:.4f}" if remaining is not None else "unlimited"
        status = "EXHAUSTED" if exhausted else "ok"
        click.echo(f"  {pname.value:12s} remaining={budget_str:12s} used=${usage:.4f} calls={count} [{status}]")

    # Service credits (e.g. Jina reader tokens)
    store = broker.budget_tracker._store
    if store:
        credits = store.get_all_service_credits()
        if credits:
            click.echo()
            click.echo("Service credits:")
            for service, info in credits.items():
                click.echo(f"  {service:12s} balance={info['balance']:,.0f} tokens")


@cli.command()
@click.option("--service", "-s", required=True, help="Service name (e.g. jina)")
@click.option("--balance", "-b", required=True, type=float, help="Current token balance")
def set_balance(service, balance):
    """Set a token balance for an extraction service."""
    from argus.broker.router import create_broker

    broker = create_broker()
    store = broker.budget_tracker._store
    if store is None:
        click.echo("Budget persistence not enabled. Set ARGUS_DB_PATH in .env", err=True)
        sys.exit(1)

    store.set_service_credit(service, balance)
    click.echo(f"Set {service} balance to {balance:,.0f} tokens")


@cli.command()
@click.option("--provider", "-p", required=True, help="Provider name")
@click.option("--query", "-q", default="argus", help="Test query")
def test_provider(provider, query):
    """Smoke-test a single provider."""
    from argus.broker.router import create_broker
    from argus.models import ProviderName, SearchQuery

    broker = create_broker()

    try:
        pname = ProviderName(provider)
    except ValueError:
        click.echo(f"Unknown provider: {provider}", err=True)
        sys.exit(1)

    prov = broker._providers.get(pname)
    if prov is None:
        click.echo(f"Provider not registered: {provider}", err=True)
        sys.exit(1)

    click.echo(f"Testing {pname.value}...")
    click.echo(f"  Available: {prov.is_available()}")
    click.echo(f"  Status: {prov.status().value}")

    q = SearchQuery(query=query, mode=SearchMode.DISCOVERY, max_results=3)
    results, trace = _run(prov.search(q))

    click.echo(f"  Trace: {trace.status} ({trace.results_count} results, {trace.latency_ms}ms)")
    if trace.error:
        click.echo(f"  Error: {trace.error}")
    for r in results[:3]:
        click.echo(f"    - {r.title}: {r.url}")


@cli.command()
@click.option("--host", "-h", default="127.0.0.1", help="Bind host")
@click.option("--port", "-p", default=8000, help="Bind port")
@click.option("--reload", is_flag=True, help="Auto-reload on code changes")
def serve(host, port, reload):
    """Start the Argus API server."""
    import os
    os.environ.setdefault("ARGUS_HOST", host)
    os.environ.setdefault("ARGUS_PORT", str(port))

    import uvicorn
    uvicorn.run("argus.api.main:app", host=host, port=port, reload=reload)


@cli.group()
def mcp():
    """Start the Argus MCP server for LLM integration."""
    pass


@mcp.command(name="serve")
@click.option("--transport", "-t", default="stdio", type=click.Choice(["stdio", "sse"]))
@click.option("--host", "-h", default="127.0.0.1", help="Host for SSE transport")
@click.option("--port", "-p", default=8001, help="Port for SSE transport")
def mcp_serve(transport, host, port):
    """Start MCP server. Use stdio for Claude/Cursor, sse for remote access."""
    from argus.mcp.server import serve_mcp
    serve_mcp(transport=transport, host=host, port=port)
