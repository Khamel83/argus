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
    from argus.models import ProviderName

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
    """Configure and run the Argus MCP server."""
    pass


@mcp.command(name="serve")
@click.option("--transport", "-t", default="stdio", type=click.Choice(["stdio", "sse"]))
@click.option("--host", "-h", default="127.0.0.1", help="Host for SSE transport")
@click.option("--port", "-p", default=8001, help="Port for SSE transport")
def mcp_serve(transport, host, port):
    """Start MCP server. Use stdio for Claude/Cursor, sse for remote access."""
    from argus.mcp.server import serve_mcp
    serve_mcp(transport=transport, host=host, port=port)


@mcp.command(name="init")
@click.option("--global", "global_", is_flag=True, help="Add to ~/.claude.json (all projects)")
def mcp_init(global_):
    """Add Argus MCP server config to this project or globally."""
    import json
    import sys
    from pathlib import Path

    argus_bin = str(Path(sys.argv[0]).resolve())
    entry = {
        "command": argus_bin,
        "args": ["mcp", "serve"],
        "description": "Argus search broker — search_web, extract_content, recover_url, expand_links, health, budgets",
    }

    if global_:
        config_path = Path.home() / ".claude.json"
        scope = "global (~/.claude.json)"
    else:
        config_path = Path(".mcp.json")
        scope = "project (.mcp.json)"

    config_path.touch(mode=0o644, exist_ok=True)
    try:
        data = json.loads(config_path.read_text()) if config_path.stat().st_size else {}
    except json.JSONDecodeError:
        click.echo(f"Warning: {config_path} had invalid JSON, starting fresh.", err=True)
        data = {}

    servers = data.setdefault("mcpServers", {})

    if "argus" in servers:
        servers["argus"] = entry
        action = "Updated"
    else:
        servers["argus"] = entry
        action = "Added"

    config_path.write_text(json.dumps(data, indent=2) + "\n")
    click.echo(f"{action} argus MCP to {scope}")
    click.echo("Restart Claude Code in this project to connect.")


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
            click.echo(f"\nDrop EditThisCookie JSON exports there, then re-run this command.")
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
    click.echo(f"Run 'argus cookies health' to check status anytime.")


@cookies.command(name="health")
def cookies_health():
    """Show health status of all cookie domains."""
    from argus.extraction.cookies import get_health_summary, COOKIE_DIR, get_cookie_path

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
