"""
Argus CLI — command-line interface to the search broker.
"""

import asyncio
import json
import sys

import click

from argus.logging import get_logger

logger = get_logger("cli")

_STATUS_DISPLAY = {
    "enabled": "OK",
    "disabled_by_config": "DISABLED (config)",
    "unavailable_missing_key": "MISSING KEY",
    "temporarily_disabled_after_failures": "COOLDOWN",
    "budget_exhausted": "BUDGET EXHAUSTED",
    "degraded": "DEGRADED",
    "healthy": "HEALTHY",
}


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


def _emit_json(payload):
    click.echo(json.dumps(payload, indent=2))


def _workflow_to_dict(result):
    return {
        "run_id": result.run_id,
        "kind": result.kind.value,
        "status": result.status.value,
        "target": result.target,
        "created_at": result.created_at.isoformat() if result.created_at else None,
        "started_at": result.started_at.isoformat() if result.started_at else None,
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
        "status_url": result.status_url,
        "snapshot_dir": result.snapshot_dir,
        "report_path": result.report_path,
        "manifest_path": result.manifest_path,
        "artifacts": [artifact.__dict__ for artifact in result.artifacts],
        "documents": [
            {
                **document.__dict__,
                "egress": getattr(document, "egress", None),
                "machine": getattr(document, "machine", None),
            }
            for document in result.documents
        ],
        "citations": [citation.__dict__ for citation in result.citations],
        "summary_sections": [section.__dict__ for section in result.summary_sections],
        "metadata": result.metadata,
        "error": result.error,
    }


def _print_workflow_result(result, as_json: bool):
    if as_json:
        _emit_json(_workflow_to_dict(result))
        return

    click.echo(f"Run: {result.run_id}")
    click.echo(f"Workflow: {result.kind.value}")
    click.echo(f"Status: {result.status.value}")
    click.echo(f"Target: {result.target}")
    click.echo(f"Snapshot: {result.snapshot_dir}")
    if result.report_path:
        click.echo(f"Report: {result.report_path}")
    if result.manifest_path:
        click.echo(f"Manifest: {result.manifest_path}")
    if result.error:
        click.echo(f"Error: {result.error}")
    if result.summary_sections:
        click.echo()
        for section in result.summary_sections:
            click.echo(section.heading)
            click.echo(section.body)
            if section.citation_ids:
                click.echo(f"Citations: {', '.join(section.citation_ids)}")
            click.echo()


@click.group()
@click.version_option(package_name="argus")
def cli():
    """Argus — standalone search broker."""
    pass


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def paths(as_json):
    """Show the resolved Argus runtime storage paths."""
    from argus.corpus import describe_corpus_paths

    payload = describe_corpus_paths()
    if as_json:
        _emit_json(payload)
        return

    click.echo("Argus data paths:")
    for key, value in payload.items():
        click.echo(f"  {key}: {value}")


@cli.command()
@click.option("--query", "-q", required=True, help="Search query")
@click.option("--mode", "-m", default="discovery", type=click.Choice(["recovery", "discovery", "grounding", "research"]),
              help="recovery (find dead URLs), discovery (general search), grounding (fact-checking), research (deep multi-provider)")
@click.option("--max-results", "-n", default=10, help="Max results")
@click.option("--providers", "-p", multiple=False, help="Override providers (comma-separated)")
@click.option("--session", "-s", default=None, help="Session ID for multi-turn context")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def search(query, mode, max_results, providers, as_json, session):
    """Execute a search query.

    Modes:
      recovery    Find a moved or dead URL by title/domain hints
      discovery   General web search across all available providers
      grounding   Fact-checking and finding authoritative sources
      research    Deep multi-provider search for research tasks
    """
    from argus.broker.router import create_broker
    from argus.models import ProviderName, SearchMode, SearchQuery

    broker = create_broker()
    override = [ProviderName(item.strip()) for item in providers.split(",")] if providers else None
    q = SearchQuery(query=query, mode=SearchMode(mode), max_results=max_results, providers=override)

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
                {
                    "url": r.url,
                    "title": r.title,
                    "snippet": r.snippet,
                    "provider": r.provider.value if r.provider else None,
                    "score": r.score,
                    "egress": r.metadata.get("egress") if r.metadata else None,
                    "machine": r.metadata.get("machine") if r.metadata else None,
                }
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

        if resp.budget_warnings:
            click.echo("Budget warnings:")
            for w in resp.budget_warnings:
                click.echo(f"  {w}")


@cli.command()
@click.option("--url", "-u", required=True, help="URL to extract content from")
@click.option("--domain", "-d", help="Domain hint for authenticated extraction (e.g. nytimes.com)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def extract(url, as_json, domain):
    """Extract clean text content from a URL."""
    from argus.extraction import extract_url

    result = _run(extract_url(url, domain=domain))

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
            "egress": result.egress,
            "machine": result.machine,
            "source_type": result.source_type,
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


@cli.command(name="recover-article")
@click.option("--url", "-u", required=True, help="Dead or moved article URL")
@click.option("--title", "-t", default=None, help="Optional title hint")
@click.option("--domain", "-d", default=None, help="Optional domain hint")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def recover_article(url, title, domain, as_json):
    """Recover a dead article into a citation-backed local report."""
    from argus.broker.router import create_broker
    from argus.workflows import WorkflowService

    service = WorkflowService(create_broker())
    result = _run(service.recover_article(url=url, title=title, domain=domain))
    _print_workflow_result(result, as_json)


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


@cli.command(name="capture-site")
@click.option("--url", "-u", required=True, help="Site root or docs root URL")
@click.option("--soft-page-limit", default=75, type=int, help="Preferred page budget")
@click.option("--hard-page-limit", default=200, type=int, help="Maximum page budget")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def capture_site(url, soft_page_limit, hard_page_limit, as_json):
    """Capture the important parts of a site and summarize them with references."""
    from argus.broker.router import create_broker
    from argus.workflows import WorkflowService

    service = WorkflowService(create_broker())
    result = _run(
        service.capture_site(
            url=url,
            soft_page_limit=soft_page_limit,
            hard_page_limit=hard_page_limit,
        )
    )
    _print_workflow_result(result, as_json)


@cli.command(name="build-research-pack")
@click.option("--topic", "-t", required=True, help="Topic or product to research")
@click.option("--official-url", default=None, help="Optional official docs URL")
@click.option("--max-research-pages", default=40, type=int, help="Max non-official research pages")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def build_research_pack(topic, official_url, max_research_pages, as_json):
    """Build a local pack with official docs plus external research."""
    from argus.broker.router import create_broker
    from argus.workflows import WorkflowService

    service = WorkflowService(create_broker())
    result = _run(
        service.build_research_pack(
            topic=topic,
            official_url=official_url,
            max_research_pages=max_research_pages,
        )
    )
    _print_workflow_result(result, as_json)


@cli.command()
def health():
    """Show provider health status."""
    from argus.broker.router import create_broker
    from argus.models import ProviderName

    broker = create_broker()
    for pname in ProviderName:
        status = broker.get_provider_status(pname)
        effective = status["effective_status"]
        raw = effective if isinstance(effective, str) else effective.value
        click.echo(f"  {pname.value:12s} {_STATUS_DISPLAY.get(raw, raw)}")
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

        budget_str = f"{remaining:.0f} queries" if remaining is not None else "unlimited"
        usage_str = f"{usage:.0f} queries"
        status = "EXHAUSTED" if exhausted else "ok"
        click.echo(
            f"  {pname.value:12s} remaining={budget_str:14s} "
            f"used={usage_str:12s} calls={count} [{status}]"
        )

    # Token balances
    store = broker.budget_tracker._store
    if store:
        balances = store.get_all_token_balances()
        if balances:
            click.echo()
            click.echo("Token balances:")
            for service, info in balances.items():
                click.echo(f"  {service:12s} balance={info['balance']:,.0f} tokens")


@cli.command("check-balances")
def check_balances():
    """Probe all providers for live credit/balance info and cache results."""
    from argus.broker.router import create_broker
    from argus.broker.balance_check import check_all_balances, persist_balances

    broker = create_broker()

    # Collect API keys from provider configs
    api_keys = {}
    for pname, provider in broker._providers.items():
        cfg = provider._config if hasattr(provider, "_config") else None
        if cfg and getattr(cfg, "api_key", None):
            api_keys[pname] = cfg.api_key

    if not api_keys:
        click.echo("No API keys configured. Nothing to check.")
        return

    click.echo(f"Checking balances for {len(api_keys)} providers...")
    balances = asyncio.get_event_loop().run_until_complete(check_all_balances(api_keys))

    store = broker.budget_tracker._store
    persist_balances(balances, store)

    click.echo()
    for b in balances:
        if b.error:
            click.echo(f"  {b.provider.value:12s} ERROR: {b.error}")
        elif b.remaining is not None:
            limit_str = f"/{b.limit:.0f}" if b.limit else ""
            click.echo(f"  {b.provider.value:12s} {b.remaining:.0f} {b.unit} remaining {limit_str} (via {b.source})")
        else:
            click.echo(f"  {b.provider.value:12s} no credit data available")

    if store:
        click.echo(f"\nCached to {broker.budget_tracker._store._db_path}")
    click.echo("\nRun 'argus budgets' to see combined status.")


@cli.command()
@click.option("--service", "-s", required=True, help="Service name (e.g. jina)")
@click.option("--balance", "-b", required=True, type=float, help="Current token balance")
def set_balance(service, balance):
    """Set a token balance for an extraction service."""
    from argus.broker.router import create_broker

    broker = create_broker()
    store = broker.budget_tracker._store
    if store is None:
        click.echo("Budget persistence not enabled. Set ARGUS_BUDGET_DB_PATH in .env", err=True)
        sys.exit(1)

    store.set_token_balance(service, balance)
    click.echo(f"Set {service} balance to {balance:,.0f} tokens")


@cli.command()
@click.option("--provider", "-p", required=True, help="Provider name")
@click.option("--query", "-q", default="argus", help="Test query")
def test_provider(provider, query):
    """Smoke-test a single provider."""
    from argus.broker.router import create_broker
    from argus.models import ProviderName, SearchMode, SearchQuery

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
    click.echo(f"  Status: {_STATUS_DISPLAY.get(prov.status().value, prov.status().value)}")
    if not prov.is_available():
        click.echo("  Skipped: provider is not available")
        return

    q = SearchQuery(query=query, mode=SearchMode.DISCOVERY, max_results=3)
    results, trace = _run(prov.search(q))

    click.echo(f"  Trace: {trace.status} ({trace.results_count} results, {trace.latency_ms}ms)")
    if trace.error:
        click.echo(f"  Error: {trace.error}")
    for r in results[:3]:
        click.echo(f"    - {r.title}: {r.url}")


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def doctor(as_json):
    """Diagnose your Argus setup: config, providers, connectivity, and MCP readiness."""
    from argus.broker.router import create_broker
    from argus.models import ProviderName

    broker = create_broker()
    checks = []

    # 1. Config loads
    try:
        from argus.config import get_config
        cfg = get_config()
        checks.append(("Config", True, f"env={cfg.env}, log={cfg.log_level}"))
    except Exception as e:
        checks.append(("Config", False, str(e)))

    # 2. Provider audit
    ready = 0
    needs_key = 0
    for pname in ProviderName:
        status = broker.get_provider_status(pname)
        raw = status["effective_status"]
        display = _STATUS_DISPLAY.get(raw if isinstance(raw, str) else raw.value, str(raw))
        if display == "OK" or display == "HEALTHY":
            ready += 1
        elif display == "MISSING KEY":
            needs_key += 1
    checks.append(("Providers", ready > 0, f"{ready} ready, {needs_key} need API keys"))

    # 3. SearXNG connectivity (HEAD request)
    try:
        import urllib.request
        import urllib.error
        cfg = get_config()
        if cfg.searxng.enabled:
            req = urllib.request.Request(cfg.searxng.base_url, method="HEAD")
            urllib.request.urlopen(req, timeout=5)
            checks.append(("SearXNG", True, f"reachable at {cfg.searxng.base_url}"))
        else:
            checks.append(("SearXNG", None, "disabled (enable in .env if you have Docker)"))
    except Exception:
        checks.append(("SearXNG", False, "not reachable — check Docker container"))

    # 4. DuckDuckGo probe
    try:
        from argus.providers.duckduckgo import DuckDuckGoProvider
        ddg = DuckDuckGoProvider()
        checks.append(("DuckDuckGo", ddg.is_available(), "available" if ddg.is_available() else "not available"))
    except Exception as e:
        checks.append(("DuckDuckGo", False, str(e)))

    # 5. MCP package
    try:
        import mcp.server.fastmcp  # noqa: F401
        checks.append(("MCP package", True, "installed"))
    except ImportError:
        checks.append(("MCP package", False, "pip install 'argus-search[mcp]'"))

    if as_json:
        _emit_json({
            "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
            "providers_ready": ready,
            "providers_need_keys": needs_key,
        })
        return

    # Human output
    all_pass = True
    for name, ok, detail in checks:
        if ok is None:
            icon = "-"
            status = "SKIP"
        elif ok:
            icon = "+"
            status = "OK"
        else:
            icon = "!"
            status = "FAIL"
            all_pass = False
        click.echo(f"  [{icon}] {name:15s} {status:5s} {detail}")
    click.echo()
    if all_pass:
        click.echo("Setup looks good. Run 'argus health' for detailed provider status.")
    else:
        click.echo("Some checks failed. See above for details.")
        if needs_key:
            click.echo(f"  {needs_key} providers need API keys — add them to .env or secrets vault.")
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
    """Configure and run the Argus MCP server."""
    pass


@mcp.command(name="serve")
@click.option("--transport", "-t", default="stdio", type=click.Choice(["stdio", "sse", "streamable-http"]))
@click.option("--host", "-h", default="127.0.0.1", help="Host for SSE/streamable-http transport")
@click.option("--port", "-p", default=8001, help="Port for SSE/streamable-http transport")
def mcp_serve(transport, host, port):
    """Start MCP server. Use stdio for Claude/Cursor, sse or streamable-http for remote access."""
    try:
        from argus.mcp.server import serve_mcp
    except ImportError:
        raise SystemExit(
            "MCP extras not installed. Run: pip install 'argus-search[mcp]'"
        )
    serve_mcp(transport=transport, host=host, port=port)


@mcp.command(name="init")
@click.option("--global", "global_", is_flag=True, help="Add to ~/.claude.json (all projects, Claude Code only)")
@click.option("--client", default="all", type=click.Choice(["all", "claude", "opencode", "gemini", "codex"]),
              help="Target client (default: all)")
@click.option("--url", "remote_url", default=None, envvar="ARGUS_REMOTE_URL",
              help="Remote Argus server URL (e.g. http://100.x.x.x:8271). "
                   "Also reads ARGUS_REMOTE_URL env var. If set, generates remote config instead of local stdio.")
@click.option("--key", "api_key", default=None, envvar="ARGUS_API_KEY",
              help="API key for remote server. Also reads ARGUS_API_KEY env var.")
@click.option("--transport", "-t", default="streamable-http", type=click.Choice(["sse", "streamable-http"]),
              help="Transport for remote config (default: streamable-http)")
def mcp_init(global_, client, remote_url, api_key, transport):
    """Add Argus MCP server config to this project or globally.

    By default writes a local stdio config to .mcp.json (Claude Code, OpenCode, Cursor).
    Pass --url (or set ARGUS_REMOTE_URL) to generate a remote config instead.

    \b
    Examples:
      argus mcp init                                    # local stdio
      argus mcp init --url http://argus.local:8271      # remote streamable-http
      argus mcp init --url http://argus.local:8271 -t sse # remote sse
      argus mcp init --client gemini                    # print gemini mcp add command only
    """
    import sys
    from pathlib import Path

    argus_bin = str(Path(sys.argv[0]).resolve())

    if remote_url:
        path = "/mcp" if transport == "streamable-http" else "/sse"
        mcp_url = remote_url.rstrip("/") + path
        entry = {"type": "http" if transport == "streamable-http" else "sse", "url": mcp_url}
        if api_key:
            entry["headers"] = {"Authorization": f"Bearer {api_key}"}
        mode = f"remote {transport} ({mcp_url})"
    else:
        entry = {
            "command": argus_bin,
            "args": ["mcp", "serve"],
            "description": "Argus search broker",
        }
        mode = "local stdio"

    write_json = client in ("all", "claude", "opencode")

    if write_json:
        # 1. Claude Code & OpenCode (global ~/.claude.json or project .mcp.json)
        # 2. Cursor (global ~/.cursor/mcp.json or project .cursor/mcp.json)
        paths = []
        if global_:
            paths.append(Path.home() / ".claude.json")
            paths.append(Path.home() / ".cursor" / "mcp.json")
            # Claude Desktop (macOS and Linux)
            if sys.platform == "darwin":
                paths.append(Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
            else:
                paths.append(Path.home() / ".config" / "Claude" / "claude_desktop_config.json")
            scope_name = "global"
        else:
            paths.append(Path(".mcp.json"))
            paths.append(Path(".cursor") / "mcp.json")
            scope_name = "project"

        updated_paths = []
        for config_path in paths:
            # For .cursor/mcp.json, ensure directory exists
            if config_path.name == "mcp.json" and config_path.parent.name == ".cursor":
                if global_:
                    # Only write global cursor config if ~/.cursor exists
                    if not config_path.parent.exists():
                        continue
                else:
                    # Create .cursor in project root if it doesn't exist
                    config_path.parent.mkdir(exist_ok=True)

            config_path.touch(mode=0o644, exist_ok=True)
            try:
                data = json.loads(config_path.read_text()) if config_path.stat().st_size else {}
            except json.JSONDecodeError:
                data = {}

            servers = data.setdefault("mcpServers", {})
            if "argus" in servers and servers["argus"] == entry:
                updated_paths.append(str(config_path))
                continue

            if "argus" in servers:
                if not click.confirm(f"argus MCP config already exists in {config_path}. Overwrite?", default=False):
                    continue

            servers["argus"] = entry
            config_path.write_text(json.dumps(data, indent=2) + "\n")
            updated_paths.append(str(config_path))

        if updated_paths:
            click.echo(f"Updated argus MCP ({scope_name} {mode}):")
            for p in updated_paths:
                click.echo(f"  - {p}")
        else:
            click.echo(f"No configuration files updated for {client}.")

    if client in ("all", "gemini"):
        click.echo("\nGemini CLI — run once to register:")
        if remote_url:
            path = "/mcp" if transport == "streamable-http" else "/sse"
            mcp_url = remote_url.rstrip("/") + path
            t_flag = "http" if transport == "streamable-http" else "sse"
            if api_key:
                click.echo(f"  gemini mcp add argus {mcp_url} -t {t_flag} -H \"Authorization: Bearer {api_key}\"")
            else:
                click.echo(f"  gemini mcp add argus {mcp_url} -t {t_flag}")
        else:
            click.echo(f"  gemini mcp add argus {argus_bin} mcp serve")

    if client in ("all", "codex"):
        toml_path = Path.home() / ".codex" / "config.toml"
        if not toml_path.parent.exists():
            click.echo("\nCodex — ~/.codex/ not found; is Codex installed?")
        else:
            # Read current TOML (line-based — avoid pulling in tomllib/tomli as a dep)
            toml_text = toml_path.read_text() if toml_path.exists() else ""

            if remote_url:
                path = "/mcp" if transport == "streamable-http" else "/sse"
                codex_url = remote_url.rstrip("/") + path
                new_section = (
                    f"\n[mcp_servers.argus]\n"
                    f'url = "{codex_url}"\n'
                    f'bearer_token_env_var = "ARGUS_API_KEY"\n'
                )
            else:
                new_section = (
                    f"\n[mcp_servers.argus]\n"
                    f'command = "{argus_bin}"\n'
                    f'args = ["mcp", "serve"]\n'
                )

            if "[mcp_servers.argus]" in toml_text:
                # Remove old section (everything from [mcp_servers.argus] to next [section])
                import re
                toml_text = re.sub(
                    r"\n\[mcp_servers\.argus\][^\[]*",
                    new_section,
                    toml_text,
                )
            else:
                toml_text = toml_text.rstrip("\n") + new_section

            toml_path.write_text(toml_text)
            click.echo(f"\nCodex — updated ~/.codex/config.toml with argus MCP ({remote_url or 'local stdio'})")

            # Ensure ARGUS_API_KEY is exported in shell profile (Codex reads it as env var)
            if api_key and remote_url:
                zshrc = Path.home() / ".zshrc"
                bashrc = Path.home() / ".bashrc"
                rc_path = zshrc if zshrc.exists() else bashrc
                rc_text = rc_path.read_text() if rc_path.exists() else ""
                if "ARGUS_API_KEY" not in rc_text:
                    with rc_path.open("a") as f:
                        f.write(f"\n# Argus MCP bearer token\nexport ARGUS_API_KEY={api_key}\n")
                    click.echo(f"  Added ARGUS_API_KEY to {rc_path.name} (run: source ~/{rc_path.name})")
                else:
                    click.echo(f"  ARGUS_API_KEY already in {rc_path.name}")

    if client == "all":
        click.echo("\nRestart your AI client to connect.")


@mcp.command(name="check")
def mcp_check():
    """Validate MCP server setup: package, transport, and authentication."""
    from pathlib import Path

    checks = []

    # 1. MCP package
    try:
        import mcp.server.fastmcp  # noqa: F401
        checks.append(("MCP package", True, "installed"))
    except ImportError:
        checks.append(("MCP package", False, "pip install 'argus-search[mcp]'"))

    # 2. FastMCP Context (for progress notifications)
    try:
        from mcp.server.fastmcp import Context  # noqa: F401
        checks.append(("Progress notifications", True, "Context available"))
    except Exception:
        checks.append(("Progress notifications", False, "MCP version may not support Context"))

    # 3. Config file exists
    config_paths = [Path(".mcp.json"), Path.home() / ".claude.json"]
    config_found = [p for p in config_paths if p.exists()]
    has_argus = False
    for p in config_found:
        try:
            data = json.loads(p.read_text())
            if "mcpServers" in data and "argus" in data["mcpServers"]:
                has_argus = True
                break
        except Exception:
            pass
    checks.append(("MCP config file", has_argus, f"found in {p}" if has_argus else "run 'argus mcp init'"))

    # 4. API key for remote access
    from argus.auth import AuthConfig
    auth = AuthConfig.from_env()
    checks.append(("ARGUS_API_KEY (remote)", auth.has_caller_key(), "set" if auth.has_caller_key() else "needed for SSE/streamable-http transport"))

    # Report
    all_ok = True
    for name, ok, detail in checks:
        status = "OK" if ok else "MISSING"
        if not ok:
            all_ok = False
        click.echo(f"  {name:30s} {status:8s} {detail}")
    click.echo()
    if all_ok:
        click.echo("MCP setup is ready.")
    else:
        click.echo("Fix the issues above, then restart Claude Code.")


@cli.group()
def corpus():
    """Manage Argus corpus storage and legacy imports."""
    pass


@corpus.command(name="import-docs-cache")
@click.option("--source", "-s", required=True, type=click.Path(exists=True), help="Path to legacy docs-cache root")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def import_docs_cache(source, as_json):
    """Import a legacy docs-cache tree into Argus-owned storage."""
    from argus.broker.router import create_broker
    from argus.workflows import WorkflowService

    service = WorkflowService(create_broker())
    payload = service.import_legacy_docs_cache(source)
    if as_json:
        _emit_json(payload)
        return
    click.echo("Imported legacy docs-cache:")
    for key, value in payload.items():
        click.echo(f"  {key}: {value}")


@cli.group()
def cookies():
    """Manage browser cookies for authenticated extraction."""
    pass


@cookies.command(name="import")
@click.option("--domain", "-d", default=None, help="Domain (e.g. nytimes.com). Inferred from cookies if omitted.")
@click.option("--file", "-f", "filepath", default=None, type=click.Path(exists=True), help="EditThisCookie JSON file. If omitted, imports all from inbox.")
def cookies_import(domain, filepath):
    """Import cookies from EditThisCookie JSON exports.

    Drop JSON files in ~/.config/argus/cookies/inbox/ then run:
        argus cookies import

    Or import a specific file:
        argus cookies import -f ~/Downloads/nyt_cookies.json

    Domain is auto-detected from cookie data. Override with -d if needed.
    """
    from pathlib import Path
    from argus.extraction.cookies import COOKIE_DIR, load_editthiscookie_json, _load_health, _save_health

    cookie_dir = COOKIE_DIR
    inbox_dir = cookie_dir / "inbox"
    cookie_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir.mkdir(parents=True, exist_ok=True)

    # Collect files to process
    if filepath:
        files = [Path(filepath)]
    elif inbox_dir.exists():
        files = sorted(inbox_dir.glob("*.json"))
        if not files:
            click.echo(f"No cookie files found in {inbox_dir}")
            click.echo("\nDrop EditThisCookie JSON exports there, then re-run this command.")
            return
    else:
        click.echo(f"No inbox directory at {inbox_dir}")
        click.echo(f"Drop cookie JSON files in: {inbox_dir}")
        return

    from datetime import datetime, timezone
    imported = 0

    for f in files:
        # Load raw to infer domain
        try:
            raw = json.loads(f.read_text())
        except Exception as e:
            click.echo(f"  SKIP {f.name}: invalid JSON ({e})")
            continue

        # Get cookies list (handle both array and wrapped object)
        if isinstance(raw, dict):
            cookie_list = raw.get("cookies", [raw])
        else:
            cookie_list = raw

        # Infer domain from cookie data
        inferred = domain
        if not inferred:
            domains_seen = set()
            for c in cookie_list:
                d = c.get("domain", "")
                # Strip leading dots, skip empty
                d = d.lstrip(".")
                if d and not d.startswith(" "):
                    # Get base domain (last 2 parts)
                    parts = d.split(".")
                    if len(parts) >= 2:
                        base = ".".join(parts[-2:])
                        if base not in ("co", "com", "org", "net", "io"):
                            domains_seen.add(base)
                    else:
                        domains_seen.add(d)

            if not domains_seen:
                click.echo(f"  SKIP {f.name}: no domain found in cookies")
                continue

            # Pick the most common base domain
            from collections import Counter
            inferred = Counter(domains_seen).most_common(1)[0][0]

        dest = cookie_dir / f"{inferred}.json"

        # Validate by doing a test load
        loaded = load_editthiscookie_json(f)
        if not loaded:
            click.echo(f"  SKIP {f.name}: no valid cookies")
            continue

        # Copy to destination
        import shutil
        shutil.copy2(f, dest)

        # Record health
        health = _load_health()
        health[inferred] = {
            "status": "healthy",
            "request_count": 0,
            "last_used": None,
            "cookies_loaded_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_health(health)

        # Remove from inbox if it was there
        if f.parent == inbox_dir:
            f.unlink()

        click.echo(f"  OK {inferred}: {len(loaded)} cookies from {f.name}")
        imported += 1

    click.echo(f"\nImported {imported} cookie file(s)")
    click.echo(f"Cookie dir: {cookie_dir}")
    click.echo("Run 'argus cookies health' to check status anytime.")


@cookies.command(name="health")
def cookies_health():
    """Show health status of all cookie domains."""
    from argus.extraction.cookies import get_health_summary, COOKIE_DIR

    summary = get_health_summary()

    if not summary:
        click.echo("No cookies configured.")
        click.echo(f"\nCookie directory: {COOKIE_DIR}")
        click.echo("Import cookies with: argus cookies import -d nytimes.com -f cookies.json")
        return

    click.echo("Cookie health:\n")
    for domain, info in summary.items():
        status_emoji = "OK" if info["status"] == "healthy" else "STALE"
        age = f"{info['days_since_used']}d ago" if info['days_since_used'] is not None else "never"
        warning = " [REFRESH NEEDED]" if info.get("stale_warning") else ""
        click.echo(f"  {domain:30s} [{status_emoji:5s}]  used: {age},  requests: {info['request_count']}{warning}")

    # Show what cookies are available on disk
    click.echo(f"\nCookie directory: {COOKIE_DIR}")
    if COOKIE_DIR.exists():
        files = sorted(f.stem for f in COOKIE_DIR.glob("*.json") if f.stem != "health")
        if files:
            click.echo(f"On disk: {', '.join(files)}")
