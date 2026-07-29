"""Explicit Tier-1/no-server CLI implementations.

This module owns direct local execution. The production CLI path never imports
it when an authenticated HTTP authority is configured.
"""

from __future__ import annotations

import asyncio
import json
import os

import click


def _run(coro):
    return asyncio.run(coro)


def search(
    *,
    query,
    mode,
    max_results,
    providers,
    as_json,
    session,
    attribution,
    free_only,
    caller,
):
    from argus.broker.router import create_broker
    from argus.models import ProviderName, SearchMode, SearchQuery

    broker = create_broker()
    override = (
        [ProviderName(item.strip()) for item in providers.split(",")]
        if providers
        else None
    )
    request = SearchQuery(
        query=query,
        mode=SearchMode(mode),
        max_results=max_results,
        providers=override,
        free_only=free_only,
        caller=caller,
    )
    if session:
        response, session_id = _run(
            broker.search_with_session(
                request,
                session_id=session,
                compute_attribution=attribution,
            )
        )
    else:
        response = _run(broker.search(request, compute_attribution=attribution))
        session_id = None

    payload = {
        "query": response.query,
        "mode": response.mode.value,
        "results": [
            {
                "url": item.url,
                "title": item.title,
                "snippet": item.snippet,
                "provider": item.provider.value if item.provider else None,
                "score": item.score,
                "score_attribution": (item.score_attribution if attribution else None),
                "egress": item.metadata.get("egress") if item.metadata else None,
                "machine": item.metadata.get("machine") if item.metadata else None,
            }
            for item in response.results
        ],
        "total_results": response.total_results,
        "cached": response.cached,
        "run_id": response.search_run_id,
    }
    if session_id:
        payload["session_id"] = session_id
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return

    click.echo(f"Query: {response.query}")
    click.echo(
        f"Mode: {response.mode.value} | Results: {response.total_results} | "
        f"Cached: {response.cached}"
    )
    click.echo(f"Run ID: {response.search_run_id}")
    if session_id:
        click.echo(f"Session: {session_id}")
    click.echo()
    for index, item in enumerate(response.results, 1):
        provider = f" [{item.provider.value}]" if item.provider else ""
        click.echo(f"  {index}. {item.title}{provider}")
        click.echo(f"     {item.url}")
        if item.snippet:
            click.echo(f"     {item.snippet[:120]}")
        click.echo()


def extract(*, url, domain, mode, as_json):
    from argus.extraction import extract_url

    result = _run(extract_url(url, domain=domain, mode=mode, caller="cli"))
    if result.error:
        click.echo(f"Error: {result.error}", err=True)
        raise click.exceptions.Exit(1)
    if as_json:
        click.echo(
            json.dumps(
                {
                    "url": result.url,
                    "title": result.title,
                    "text": result.text,
                    "author": result.author,
                    "date": result.date,
                    "word_count": result.word_count,
                    "extractor": result.extractor.value if result.extractor else None,
                    "mode": mode,
                    "egress": result.egress,
                    "machine": result.machine,
                    "source_type": result.source_type,
                },
                indent=2,
            )
        )
        return
    if result.title:
        click.echo(f"Title: {result.title}")
    if result.author:
        click.echo(f"Author: {result.author}")
    if result.date:
        click.echo(f"Date: {result.date}")
    click.echo(
        f"Words: {result.word_count} | "
        f"Extractor: {result.extractor.value if result.extractor else 'unknown'}"
    )
    click.echo()
    click.echo(result.text)


def recover_url(*, url, title, domain, as_json):
    from argus.broker.router import create_broker
    from argus.models import SearchMode, SearchQuery

    parts = [url, *(item for item in (title, domain) if item)]
    response = _run(
        create_broker().search(
            SearchQuery(
                query=" ".join(parts),
                mode=SearchMode.RECOVERY,
                max_results=10,
            )
        )
    )
    payload = {
        "url": url,
        "results": [
            {"url": item.url, "title": item.title, "snippet": item.snippet}
            for item in response.results
        ],
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return
    click.echo(f"Recovery for: {url}")
    click.echo(f"Results: {response.total_results}")
    for index, item in enumerate(response.results, 1):
        click.echo(f"  {index}. {item.title}")
        click.echo(f"     {item.url}")


def test_provider(
    *, provider, query, live, idempotency_key, durable_receipt, spend_reserved
):
    from argus.broker.budgets import PROVIDER_TIERS
    from argus.broker.execution import conservative_charge_estimate
    from argus.broker.readiness import ProbeAuthorization
    from argus.broker.router import create_broker
    from argus.models import ProviderName, SearchMode, SearchQuery

    del spend_reserved  # Preserved CLI option; standalone probes never reserve spend.
    try:
        provider_name = ProviderName(provider)
    except ValueError as exc:
        raise click.ClickException(f"Unknown provider: {provider}") from exc
    broker = create_broker()
    click.echo(f"Testing {provider_name.value}...")
    if not live:
        decision = broker.readiness_service.authorize_probe(provider_name, "fixture")
        snapshot = broker.provider_readiness_projection(provider_name)
        click.echo(
            f"  Fixture: {'verified' if decision.allowed else 'denied'} "
            f"(readiness={snapshot['state']})"
        )
        return
    request = SearchQuery(
        query=query,
        mode=SearchMode.DISCOVERY,
        max_results=3,
        providers=[provider_name],
        caller="local-cli",
        user_visible=False,
    )
    authorization = ProbeAuthorization(
        workflow="explicit_validation",
        provider=provider_name,
        named_quota=(
            "free_provider_request" if PROVIDER_TIERS[provider_name] == 0 else None
        ),
        idempotency_key=idempotency_key,
        durable_receipt=durable_receipt,
        conservative_charge=(
            conservative_charge_estimate(provider_name, request)
            if PROVIDER_TIERS[provider_name] > 0
            else None
        ),
    )
    kind = "no_money_quota" if PROVIDER_TIERS[provider_name] == 0 else "billable_search"
    decision = broker.readiness_service.authorize_probe(
        provider_name,
        kind,
        authorization,
    )
    if not decision.allowed:
        raise click.ClickException(decision.reason)
    request = SearchQuery(
        query=query,
        mode=SearchMode.DISCOVERY,
        max_results=3,
        providers=[provider_name],
        caller="local-cli",
        user_visible=False,
        metadata={
            "caller_label": "cli-smoke",
            "probe_receipt": durable_receipt,
            "probe_idempotency_key": idempotency_key,
            "probe_provider": provider_name.value,
            "probe_no_fallback": True,
            "probe_attempt_id": decision.attempt_id,
        },
    )
    response = _run(broker.search(request))
    for trace in response.traces:
        click.echo(
            f"  Trace: {trace.status} "
            f"({trace.results_count} results, {trace.latency_ms}ms)"
        )
        if trace.error:
            click.echo(f"  Error: {trace.error}")
    for item in response.results[:3]:
        click.echo(f"    - {item.title}: {item.url}")


def check_balances():
    from argus.broker.balance_check import check_all_balances, persist_balances
    from argus.broker.router import create_broker

    broker = create_broker()
    api_keys = {}
    for provider_name, provider in broker._providers.items():
        config = getattr(provider, "_config", None)
        if config and getattr(config, "api_key", None):
            api_keys[provider_name] = config.api_key
    if not api_keys:
        click.echo("No API keys configured. Nothing to check.")
        return
    click.echo(f"Checking balances for {len(api_keys)} providers...")
    balances = _run(check_all_balances(api_keys))
    store = broker.budget_tracker._store
    persist_balances(balances, store)
    click.echo()
    for balance in balances:
        if balance.error:
            click.echo(f"  {balance.provider.value:12s} ERROR: {balance.error}")
        elif balance.remaining is not None:
            if balance.unit == "usd":
                limit = f"/${balance.limit:,.2f}" if balance.limit else ""
                remaining = f"${balance.remaining:,.2f}"
            else:
                limit = f"/{balance.limit:.0f}" if balance.limit else ""
                remaining = f"{balance.remaining:.0f}"
            click.echo(
                f"  {balance.provider.value:12s} {remaining} {balance.unit} "
                f"remaining {limit} (via {balance.source})"
            )
        else:
            click.echo(f"  {balance.provider.value:12s} no credit data available")
    if store:
        click.echo(f"\nCached to {store._db_path}")
    click.echo("\nRun 'argus budgets' to see combined status.")


def set_balance(*, service, balance):
    from argus.broker.router import create_broker

    store = create_broker().budget_tracker._store
    if store is None:
        click.echo(
            "Budget persistence not enabled. Set ARGUS_BUDGET_DB_PATH in .env",
            err=True,
        )
        raise click.exceptions.Exit(1)
    store.set_token_balance(service, balance)
    click.echo(f"Set {service} balance to {balance:,.0f} tokens")


def import_docs_cache(*, source, as_json):
    from argus.broker.router import create_broker
    from argus.workflows import WorkflowService

    payload = WorkflowService(create_broker()).import_legacy_docs_cache(source)
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return
    click.echo("Imported legacy docs-cache:")
    for key, value in payload.items():
        click.echo(f"  {key}: {value}")


def cookies_import(*, domain, filepath):
    from collections import Counter
    from datetime import datetime, timezone
    from pathlib import Path
    import shutil

    from argus.extraction.cookies import (
        COOKIE_DIR,
        _load_health,
        _save_health,
        load_editthiscookie_json,
    )

    inbox = COOKIE_DIR / "inbox"
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)
    files = [Path(filepath)] if filepath else sorted(inbox.glob("*.json"))
    if not files:
        click.echo(f"No cookie files found in {inbox}")
        click.echo(
            "\nDrop EditThisCookie JSON exports there, then re-run this command."
        )
        return
    imported = 0
    for file_path in files:
        try:
            raw = json.loads(file_path.read_text())
        except Exception as exc:
            click.echo(f"  SKIP {file_path.name}: invalid JSON ({exc})")
            continue
        cookie_list = raw.get("cookies", [raw]) if isinstance(raw, dict) else raw
        inferred = domain
        if not inferred:
            domains = [
                cookie.get("domain", "").lstrip(".")
                for cookie in cookie_list
                if cookie.get("domain", "").lstrip(".")
            ]
            if not domains:
                click.echo(f"  SKIP {file_path.name}: no domain found in cookies")
                continue
            inferred = Counter(domains).most_common(1)[0][0]
        loaded = load_editthiscookie_json(file_path)
        if not loaded:
            click.echo(f"  SKIP {file_path.name}: no valid cookies")
            continue
        shutil.copy2(file_path, COOKIE_DIR / f"{inferred}.json")
        health = _load_health()
        health[inferred] = {
            "status": "healthy",
            "request_count": 0,
            "last_used": None,
            "cookies_loaded_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_health(health)
        if file_path.parent == inbox:
            file_path.unlink()
        click.echo(f"  OK {inferred}: {len(loaded)} cookies from {file_path.name}")
        imported += 1
    click.echo(f"\nImported {imported} cookie file(s)")
    click.echo(f"Cookie dir: {COOKIE_DIR}")
    click.echo("Run 'argus cookies health' to check status anytime.")


def cookies_health():
    from argus.extraction.cookies import COOKIE_DIR, get_health_summary

    summary = get_health_summary()
    if not summary:
        click.echo("No cookies configured.")
        click.echo(f"\nCookie directory: {COOKIE_DIR}")
        click.echo(
            "Import cookies with: argus cookies import -d nytimes.com -f cookies.json"
        )
        return
    click.echo("Cookie health:\n")
    for domain, info in summary.items():
        status = "OK" if info["status"] == "healthy" else "STALE"
        age = (
            f"{info['days_since_used']}d ago"
            if info["days_since_used"] is not None
            else "never"
        )
        warning = " [REFRESH NEEDED]" if info.get("stale_warning") else ""
        click.echo(
            f"  {domain:30s} [{status:5s}]  used: {age},  "
            f"requests: {info['request_count']}{warning}"
        )
    click.echo(f"\nCookie directory: {COOKIE_DIR}")
    if COOKIE_DIR.exists():
        files = sorted(
            path.stem for path in COOKIE_DIR.glob("*.json") if path.stem != "health"
        )
        if files:
            click.echo(f"On disk: {', '.join(files)}")


def serve_mcp(*, transport, host, port):
    from argus.development_mcp_server import serve_development_mcp

    return serve_development_mcp(transport=transport, host=host, port=port)


def require_nonproduction() -> None:
    if os.environ.get("ARGUS_ENV", "development").strip().lower() == "production":
        raise click.ClickException(
            "Standalone CLI execution is unavailable in production"
        )
