"""
Argus CLI — command-line interface to the search broker.
"""

import asyncio
import json
import os
import secrets
import time

import click

from argus import __version__
from argus.logging import get_logger
from argus.operations.presentation import (
    budget_remaining,
    nested_status_failures,
    provider_display_state,
)

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


def _build_research_pack_payload(
    *,
    topic: str,
    official_url: str | None,
    max_research_pages: int,
    research_target_json: tuple[str, ...],
    free_only: bool,
) -> dict:
    """Parse CLI target JSON and validate the complete request before HTTP."""

    from pydantic import ValidationError

    from argus.api.schemas import BuildResearchPackWorkflowRequest

    targets = []
    for index, raw_target in enumerate(research_target_json, 1):
        try:
            target = json.loads(raw_target)
        except json.JSONDecodeError as exc:
            raise click.ClickException(
                f"Invalid --research-target-json value {index}: malformed JSON"
            ) from exc
        if not isinstance(target, dict):
            raise click.ClickException(
                f"Invalid --research-target-json value {index}: expected a JSON object"
            )
        targets.append(target)

    try:
        request = BuildResearchPackWorkflowRequest.model_validate(
            {
                "topic": topic,
                "official_url": official_url,
                "max_research_pages": max_research_pages,
                "research_targets": targets,
                "free_only": free_only,
                "caller": "cli",
            }
        )
    except ValidationError as exc:
        raise click.ClickException(
            "Invalid build-research-pack request or research target"
        ) from exc
    return request.model_dump(mode="json")


def _cli_unready(detail: str):
    """Return the one bounded v2 shape available before execution starts."""
    request_id = f"cli-{secrets.token_hex(8)}"
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


def _negotiated_http_request(authority, route: str, payload: dict):
    """Execute exactly one request against the discovered contract family."""
    from argus.contracts import validate_v2_envelope

    try:
        selection = _run(
            authority.resolve_http_contract(
                None,
                time.monotonic,
            )
        )
    except Exception:
        return "v2", _cli_unready("Argus HTTP contract discovery is unavailable")

    if selection.outcome != "ready":
        return "v2", _cli_unready("Argus HTTP contract discovery is unavailable")
    if selection.contract_version == "2.0" and selection.base_path == "/api/v2":
        try:
            response = _run(
                authority.request_v2(
                    f"{selection.base_path}{route}",
                    payload=payload,
                )
            )
            validate_v2_envelope(response)
            return "v2", response
        except Exception:
            return "v2", _cli_unready("Argus HTTP execution authority is unavailable")
    if selection.contract_version == "1" and selection.base_path == "/api":
        try:
            return "v1", _run(
                authority.request(
                    "POST",
                    f"{selection.base_path}{route}",
                    payload=payload,
                )
            )
        except Exception:
            return "v2", _cli_unready("Argus HTTP execution authority is unavailable")
    return "v2", _cli_unready("Argus HTTP contract discovery is unavailable")


def _finish_v2(envelope: dict, as_json: bool) -> None:
    """Write one envelope, then map its canonical outcome to the CLI status."""
    if as_json:
        _emit_json(envelope)
    outcome = envelope.get("outcome")
    if outcome not in {"success", "degraded", "empty"}:
        error = envelope.get("error") or {}
        code = error.get("code") or outcome or "operation_failed"
        detail = error.get("detail") or code
        click.echo(f"Argus operation failed ({code}): {detail}", err=True)
        raise click.exceptions.Exit(1)


def _print_v2_search(envelope: dict, query: str, mode: str, as_json: bool) -> None:
    """Preserve legacy search text while exposing v2 evidence identity."""
    if as_json:
        _finish_v2(envelope, as_json=True)
        return
    result = envelope.get("result") or {}
    click.echo(f"Query: {result.get('query', query)}")
    click.echo(
        f"Mode: {result.get('mode', mode)} | "
        f"Results: {result.get('total_results', 0)} | "
        f"Cached: {result.get('cached', False)}"
    )
    click.echo(f"Run ID: {result.get('search_run_id')}")
    click.echo(f"Outcome: {envelope.get('outcome', 'unready')}")
    click.echo(f"Request ID: {envelope.get('request_id', 'unknown')}")
    click.echo("Evidence: " + ("available" if result else "unavailable"))
    if result.get("session_id"):
        click.echo(f"Session: {result['session_id']}")
    click.echo()
    for index, item in enumerate(result.get("results") or [], 1):
        provider = f" [{item['provider']}]" if item.get("provider") else ""
        click.echo(f"  {index}. {item.get('title', '')}{provider}")
        click.echo(f"     {item.get('url', '')}")
        if item.get("snippet"):
            click.echo(f"     {item['snippet'][:120]}")
        click.echo()
    _finish_v2(envelope, as_json=False)


def _print_v2_extract(envelope: dict, as_json: bool) -> None:
    """Preserve legacy extraction text while exposing v2 evidence identity."""
    if as_json:
        _finish_v2(envelope, as_json=True)
        return
    result = envelope.get("result") or {}
    if result.get("title"):
        click.echo(f"Title: {result['title']}")
    if result.get("author"):
        click.echo(f"Author: {result['author']}")
    if result.get("date"):
        click.echo(f"Date: {result['date']}")
    click.echo(
        f"Words: {result.get('word_count', 0)} | "
        f"Extractor: {result.get('extractor') or 'unknown'}"
    )
    click.echo(f"Outcome: {envelope.get('outcome', 'unready')}")
    click.echo(f"Request ID: {envelope.get('request_id', 'unknown')}")
    click.echo("Evidence: " + ("available" if result else "unavailable"))
    click.echo()
    click.echo(result.get("text") or "")
    _finish_v2(envelope, as_json=False)


def _http_authority_client():
    """Return the configured HTTP authority client for remote/production CLI."""
    from argus.authority import (
        AuthorityConfigurationError,
        HttpAuthorityClient,
        adapter_execution_mode,
        authority_client_config,
    )

    mode = adapter_execution_mode()
    if mode == "http":
        try:
            config = authority_client_config(adapter="cli")
        except AuthorityConfigurationError as exc:
            raise click.ClickException(str(exc)) from exc
        return HttpAuthorityClient(config)
    if os.environ.get("ARGUS_ENV", "development").strip().lower() == "production":
        raise click.ClickException(
            "Production CLI requires ARGUS_AUTHORITY_URL and authority authentication"
        )
    return None


def _require_http_authority():
    """Require the sole execution authority for every CLI retrieval operation."""
    authority = _http_authority_client()
    if authority is None:
        raise click.ClickException(
            "CLI retrieval requires ARGUS_AUTHORITY_URL and authority authentication"
        )
    return authority


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


def _print_workflow_payload(payload: dict, as_json: bool):
    if as_json:
        _emit_json(payload)
        return
    click.echo(f"Run: {payload.get('run_id')}")
    click.echo(f"Workflow: {payload.get('kind')}")
    click.echo(f"Status: {payload.get('status')}")
    click.echo(f"Target: {payload.get('target')}")
    if payload.get("report_path"):
        click.echo(f"Report: {payload['report_path']}")
    if payload.get("manifest_path"):
        click.echo(f"Manifest: {payload['manifest_path']}")
    if payload.get("error"):
        click.echo("Error: workflow failed")


@click.group()
@click.version_option(version=__version__, prog_name="argus")
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


@cli.command(name="image-admission")
@click.option(
    "--manifest",
    "manifest_path",
    default=lambda: os.environ.get(
        "ARGUS_RUNTIME_MANIFEST", "/app/runtime-manifest.json"
    ),
    show_default="/app/runtime-manifest.json",
    type=click.Path(path_type=str),
    help="Baked runtime manifest to validate without network access.",
)
@click.option(
    "--allow-development-revision",
    is_flag=True,
    help="Validate a local image without granting production admission.",
)
def image_admission(manifest_path, allow_development_revision):
    """Validate the image's baked identity and capabilities without network access."""
    from argus.runtime_manifest import (
        RuntimeManifestError,
        admit_runtime_manifest,
    )

    try:
        manifest = admit_runtime_manifest(
            manifest_path,
            package_version=__version__,
            allow_development_revision=allow_development_revision,
        )
    except RuntimeManifestError as error:
        raise click.ClickException(str(error)) from error

    admission_status = (
        "development-validated" if allow_development_revision else "production-admitted"
    )
    click.echo(
        f"{admission_status} "
        f"revision={manifest['source_revision']} "
        f"version={manifest['package_version']} "
        f"lock={manifest['lock_sha256']}"
    )


@cli.command()
@click.option("--query", "-q", required=True, help="Search query")
@click.option(
    "--mode",
    "-m",
    default="discovery",
    type=click.Choice(["recovery", "discovery", "grounding", "research"]),
    help="recovery (find dead URLs), discovery (general search), grounding (fact-checking), research (deep multi-provider)",
)
@click.option("--max-results", "-n", default=10, help="Max results")
@click.option(
    "--providers", "-p", multiple=False, help="Override providers (comma-separated)"
)
@click.option("--session", "-s", default=None, help="Session ID for multi-turn context")
@click.option(
    "--attribution",
    is_flag=True,
    help="Show per-provider Shapley attribution for each result's score",
)
@click.option(
    "--free",
    "free_only",
    is_flag=True,
    help="Only use free (tier 0) providers: SearXNG, DuckDuckGo, Yahoo, GitHub, WolframAlpha",
)
@click.option(
    "--caller",
    default="cli",
    help="Caller identifier for attribution (e.g. project name)",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def search(
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
    """Execute a search query.

    Modes:
      recovery    Find a moved or dead URL by title/domain hints
      discovery   General web search across all available providers
      grounding   Fact-checking and finding authoritative sources
      research    Deep multi-provider search for research tasks
    """
    authority = _http_authority_client()
    if authority is None:
        from argus import standalone_cli

        return standalone_cli.search(
            query=query,
            mode=mode,
            max_results=max_results,
            providers=providers,
            as_json=as_json,
            session=session,
            attribution=attribution,
            free_only=free_only,
            caller=caller,
        )
    request = {
        "query": query,
        "mode": mode,
        "max_results": max_results,
        "include_attribution": attribution,
        "free_only": free_only,
        "caller": caller,
    }
    if providers:
        request["providers"] = [item.strip() for item in providers.split(",")]
    if session:
        request["session_id"] = session
    version, response = _negotiated_http_request(authority, "/search", request)
    if version == "v2":
        _print_v2_search(response, query, mode, as_json)
        return
    if as_json:
        output = dict(response)
        output["run_id"] = output.pop("search_run_id", None)
        _emit_json(output)
        return
    click.echo(f"Query: {response.get('query', query)}")
    click.echo(
        f"Mode: {response.get('mode', mode)} | "
        f"Results: {response.get('total_results', 0)} | "
        f"Cached: {response.get('cached', False)}"
    )
    click.echo(f"Run ID: {response.get('search_run_id')}")
    if response.get("session_id"):
        click.echo(f"Session: {response['session_id']}")
    click.echo()
    for index, result in enumerate(response.get("results") or [], 1):
        provider = f" [{result['provider']}]" if result.get("provider") else ""
        click.echo(f"  {index}. {result.get('title', '')}{provider}")
        click.echo(f"     {result.get('url', '')}")
        if result.get("snippet"):
            click.echo(f"     {result['snippet'][:120]}")
        click.echo()


@cli.command()
@click.option("--url", "-u", required=True, help="URL to extract content from")
@click.option(
    "--domain", "-d", help="Domain hint for authenticated extraction (e.g. nytimes.com)"
)
@click.option(
    "--mode",
    "-m",
    default="default",
    type=click.Choice(["default", "archive_ingest"]),
    help="Extraction mode: default or archive_ingest",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def extract(url, domain, mode, as_json):
    """Extract clean text content from a URL."""
    authority = _http_authority_client()
    if authority is None:
        from argus import standalone_cli

        return standalone_cli.extract(
            url=url,
            domain=domain,
            mode=mode,
            as_json=as_json,
        )
    version, result = _negotiated_http_request(
        authority,
        "/extract",
        {
            "url": url,
            "domain": domain,
            "mode": mode,
            "caller": "cli",
        },
    )
    if version == "v2":
        _print_v2_extract(result, as_json)
        return
    if as_json:
        _emit_json(result)
        return
    if result.get("error"):
        raise click.ClickException("Extraction failed")
    if result.get("title"):
        click.echo(f"Title: {result['title']}")
    if result.get("author"):
        click.echo(f"Author: {result['author']}")
    if result.get("date"):
        click.echo(f"Date: {result['date']}")
    click.echo(
        f"Words: {result.get('word_count', 0)} | "
        f"Extractor: {result.get('extractor') or 'unknown'}"
    )
    click.echo()
    click.echo(result.get("text") or "")


@cli.command(name="recover-article")
@click.option("--url", "-u", required=True, help="Dead or moved article URL")
@click.option("--title", "-t", default=None, help="Optional title hint")
@click.option("--domain", "-d", default=None, help="Optional domain hint")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def recover_article(url, title, domain, as_json):
    """Recover a dead article into a citation-backed local report."""
    authority = _require_http_authority()
    result = _run(
        authority.request(
            "POST",
            "/api/workflows/recover-article",
            payload={
                "url": url,
                "title": title,
                "domain": domain,
                "caller": "cli",
            },
        )
    )
    _print_workflow_payload(result, as_json)


@cli.command(name="recover-url")
@click.option("--url", "-u", required=True, help="URL to recover")
@click.option("--title", "-t", help="Optional title hint")
@click.option("--domain", "-d", help="Optional domain hint")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def recover_url(url, title, domain, as_json):
    """Recover a dead or moved URL."""
    authority = _http_authority_client()
    if authority is None:
        from argus import standalone_cli

        return standalone_cli.recover_url(
            url=url,
            title=title,
            domain=domain,
            as_json=as_json,
        )
    version, response = _negotiated_http_request(
        authority,
        "/recover-url",
        {
            "url": url,
            "title": title,
            "domain": domain,
        },
    )
    if version == "v2":
        if as_json:
            _finish_v2(response, as_json=True)
            return
        result = response.get("result") or {}
        click.echo(f"Recovery for: {url}")
        click.echo(f"Results: {result.get('total_results', 0)}")
        click.echo(f"Outcome: {response.get('outcome', 'unready')}")
        click.echo(f"Request ID: {response.get('request_id', 'unknown')}")
        click.echo("Evidence: " + ("available" if result else "unavailable"))
        for index, item in enumerate(result.get("results") or [], 1):
            click.echo(f"  {index}. {item.get('title', '')}")
            click.echo(f"     {item.get('url', '')}")
        _finish_v2(response, as_json=False)
        return
    if as_json:
        _emit_json({"url": url, "results": response.get("results") or []})
        return
    click.echo(f"Recovery for: {url}")
    click.echo(f"Results: {response.get('total_results', 0)}")
    for index, result in enumerate(response.get("results") or [], 1):
        click.echo(f"  {index}. {result.get('title', '')}")
        click.echo(f"     {result.get('url', '')}")


@cli.command(name="capture-site")
@click.option("--url", "-u", required=True, help="Site root or docs root URL")
@click.option("--soft-page-limit", default=75, type=int, help="Preferred page budget")
@click.option("--hard-page-limit", default=200, type=int, help="Maximum page budget")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def capture_site(url, soft_page_limit, hard_page_limit, as_json):
    """Capture the important parts of a site and summarize them with references."""
    authority = _require_http_authority()
    result = _run(
        authority.request(
            "POST",
            "/api/workflows/capture-site",
            payload={
                "url": url,
                "soft_page_limit": soft_page_limit,
                "hard_page_limit": hard_page_limit,
                "caller": "cli",
            },
        )
    )
    _print_workflow_payload(result, as_json)


@cli.command(name="build-research-pack")
@click.option("--topic", "-t", required=True, help="Topic or product to research")
@click.option("--official-url", default=None, help="Optional official docs URL")
@click.option(
    "--max-research-pages", default=40, type=int, help="Max non-official research pages"
)
@click.option(
    "--research-target-json",
    multiple=True,
    help="Repeatable JSON object describing one mandatory research target",
)
@click.option(
    "--free-only",
    is_flag=True,
    help="Only use free (tier-0) providers and extractors",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def build_research_pack(
    topic,
    official_url,
    max_research_pages,
    research_target_json,
    free_only,
    as_json,
):
    """Build a local pack with official docs plus external research."""
    payload = _build_research_pack_payload(
        topic=topic,
        official_url=official_url,
        max_research_pages=max_research_pages,
        research_target_json=research_target_json,
        free_only=free_only,
    )
    path = (
        "/api/workflows/build-research-pack/start"
        if research_target_json or free_only
        else "/api/workflows/build-research-pack"
    )
    authority = _require_http_authority()
    result = _run(
        authority.request(
            "POST",
            path,
            payload=payload,
        )
    )
    _print_workflow_payload(result, as_json)


@cli.command()
def health():
    """Show provider health status."""
    authority = _http_authority_client()
    if authority is None:
        from argus import standalone_cli

        standalone_cli.require_nonproduction()
        return standalone_cli.health()
    response = _run(authority.request("GET", "/api/provider-health"))
    for provider, status in (response.get("providers") or {}).items():
        raw = provider_display_state(status)
        click.echo(f"  {provider:12s} {_STATUS_DISPLAY.get(raw, raw)}")
        for failure in nested_status_failures(status):
            click.echo(f"    - {failure}")


@cli.command()
def budgets():
    """Show provider budget status."""
    authority = _http_authority_client()
    if authority is None:
        from argus import standalone_cli

        standalone_cli.require_nonproduction()
        return standalone_cli.budgets()
    response = _run(authority.request("GET", "/api/budgets"))
    click.echo("Provider budgets:")
    for provider, summary in (response.get("providers") or {}).items():
        click.echo(
            f"  {provider:12s} remaining={budget_remaining(summary.get('remaining'))} "
            f"estimated={summary.get('argus_estimated_charge')} "
            f"uncertain={summary.get('uncertain_charge')}"
        )


@cli.command("check-balances")
def check_balances():
    """Probe balances through the standalone compatibility boundary."""
    from argus import standalone_cli

    standalone_cli.require_nonproduction()
    return standalone_cli.check_balances()


@cli.command()
@click.option("--service", "-s", required=True, help="Service name (e.g. jina)")
@click.option(
    "--balance", "-b", required=True, type=float, help="Current token balance"
)
def set_balance(service, balance):
    """Set a standalone extraction-service token balance."""
    from argus import standalone_cli

    standalone_cli.require_nonproduction()
    return standalone_cli.set_balance(service=service, balance=balance)


@cli.command()
@click.option("--provider", "-p", required=True, help="Provider name")
@click.option("--query", "-q", default="argus", help="Test query")
@click.option("--live", is_flag=True, help="Run an explicitly authorized live probe")
@click.option("--idempotency-key")
@click.option("--durable-receipt")
@click.option("--spend-reserved", is_flag=True)
def test_provider(
    provider, query, live, idempotency_key, durable_receipt, spend_reserved
):
    """Smoke-test a provider through the standalone compatibility boundary."""
    from argus import standalone_cli

    standalone_cli.require_nonproduction()
    return standalone_cli.test_provider(
        provider=provider,
        query=query,
        live=live,
        idempotency_key=idempotency_key,
        durable_receipt=durable_receipt,
        spend_reserved=spend_reserved,
    )


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def doctor(as_json):
    """Diagnose your Argus setup: config, providers, connectivity, and MCP readiness."""
    authority = _http_authority_client()
    if authority is None:
        from argus import standalone_cli

        standalone_cli.require_nonproduction()
        return standalone_cli.doctor(as_json=as_json)
    status = _run(authority.request("GET", "/api/admin/status"))
    if as_json:
        _emit_json(status)
        return
    build = status.get("build") or {}
    click.echo(
        f"Argus authority: {status.get('status', 'unknown')} "
        f"version={build.get('version', 'unknown')} "
        f"revision={build.get('source_revision', 'unknown')}"
    )
    for name, observation in sorted((status.get("dependencies") or {}).items()):
        reason = observation.get("reason")
        click.echo(
            f"  {name:15s} {observation.get('state', 'unknown')}"
            + (f" ({reason})" if reason else "")
        )
    for provider, provider_status in sorted((status.get("providers") or {}).items()):
        click.echo(f"  {provider:15s} {provider_status.get('state', 'unknown')}")
        for failure in nested_status_failures(provider_status):
            click.echo(f"    - {failure}")


@cli.command()
@click.option(
    "--host",
    "-h",
    default=None,
    help="Bind host (env: ARGUS_BIND_HOST, default: 127.0.0.1)",
)
@click.option(
    "--port",
    "-p",
    default=None,
    type=int,
    help="Bind port (env: ARGUS_PORT, default: 8000)",
)
@click.option("--reload", is_flag=True, help="Auto-reload on code changes")
def serve(host, port, reload):
    """Start the Argus API server.

    Bind address resolves in this order: CLI flag → ARGUS_BIND_HOST env → 127.0.0.1.
    Set ARGUS_BIND_HOST=0.0.0.0 in .env (or the environment) to expose externally.
    """
    import os

    bind_host = host or os.environ.get("ARGUS_BIND_HOST", "127.0.0.1")
    bind_port = port or int(os.environ.get("ARGUS_PORT", "8000"))
    os.environ.setdefault("ARGUS_HOST", bind_host)
    os.environ.setdefault("ARGUS_PORT", str(bind_port))

    import uvicorn

    uvicorn.run("argus.api.main:app", host=bind_host, port=bind_port, reload=reload)


@cli.command()
@click.option(
    "--bind",
    default=None,
    envvar="ARGUS_WORKER_BIND",
    help="Host:port to bind (default 0.0.0.0:8273)",
)
def worker(bind: str):
    """Start an Argus egress worker — minimal provider executor over HTTP."""
    import uvicorn
    from argus.worker.server import create_worker_app

    host, port = "0.0.0.0", 8273
    if bind:
        parts = bind.rsplit(":", 1)
        if len(parts) == 2:
            host, port = parts[0], int(parts[1])

    app = create_worker_app()
    uvicorn.run(app, host=host, port=port)


@cli.group()
def mcp():
    """Configure and run the Argus MCP server."""
    pass


@mcp.command(name="serve")
@click.option(
    "--transport",
    "-t",
    default="stdio",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
)
@click.option(
    "--host", "-h", default="127.0.0.1", help="Host for SSE/streamable-http transport"
)
@click.option(
    "--port", "-p", default=8001, help="Port for SSE/streamable-http transport"
)
def mcp_serve(transport, host, port):
    """Start MCP server. Use stdio for Claude/Cursor, sse or streamable-http for remote access."""
    from argus.authority import adapter_execution_mode

    try:
        if adapter_execution_mode() == "standalone":
            from argus import standalone_cli

            return standalone_cli.serve_mcp(
                transport=transport,
                host=host,
                port=port,
            )
        from argus.mcp.server import serve_mcp
    except ImportError:
        raise SystemExit(
            "MCP extras not installed. Run: pip install 'argus-search[mcp]'"
        )
    serve_mcp(transport=transport, host=host, port=port)


@mcp.command(name="init")
@click.option(
    "--global",
    "global_",
    is_flag=True,
    help="Add to ~/.claude.json (all projects, Claude Code only)",
)
@click.option(
    "--client",
    default="all",
    type=click.Choice(["all", "claude", "opencode", "gemini", "codex"]),
    help="Target client (default: all)",
)
@click.option(
    "--url",
    "remote_url",
    default=None,
    envvar="ARGUS_REMOTE_URL",
    help="Remote Argus server URL (e.g. http://100.x.x.x:8271). "
    "Also reads ARGUS_REMOTE_URL env var. If set, generates remote config instead of local stdio.",
)
@click.option(
    "--key",
    "api_key",
    default=None,
    envvar="ARGUS_API_KEY",
    help="API key for remote server. Also reads ARGUS_API_KEY env var.",
)
@click.option(
    "--transport",
    "-t",
    default="streamable-http",
    type=click.Choice(["sse", "streamable-http"]),
    help="Transport for remote config (default: streamable-http)",
)
def mcp_init(global_, client, remote_url, api_key, transport):
    """Add Argus MCP server config to this project or globally.

    By default writes a local stdio adapter config to .mcp.json (Claude Code,
    OpenCode, Cursor). The adapter requires ARGUS_AUTHORITY_URL and
    ARGUS_AUTHORITY_TOKEN in its environment. Local broker execution requires
    explicit development-only ARGUS_MCP_STANDALONE=true.
    Pass --url (or set ARGUS_REMOTE_URL) to generate a remote config instead.

    \b
    Examples:
      argus mcp init                                    # local stdio HTTP adapter
      argus mcp init --url http://argus.local:8271      # remote streamable-http
      argus mcp init --url http://argus.local:8271 -t sse # remote sse
      argus mcp init --client gemini                    # print gemini mcp add command only
    """
    import sys
    from pathlib import Path

    local_execution_mode = None
    if not remote_url:
        from argus.authority import (
            AuthorityConfigurationError,
            adapter_execution_mode,
            authority_client_config,
        )

        local_execution_mode = adapter_execution_mode()
        if local_execution_mode == "http":
            try:
                authority_client_config(adapter="mcp")
            except AuthorityConfigurationError as exc:
                raise click.ClickException(str(exc)) from exc
        elif local_execution_mode != "standalone":
            raise click.ClickException(
                "Local MCP requires ARGUS_AUTHORITY_URL and "
                "ARGUS_AUTHORITY_TOKEN; explicit development standalone "
                "requires ARGUS_MCP_STANDALONE=true"
            )

    argus_bin = str(Path(sys.argv[0]).resolve())

    if remote_url:
        path = "/mcp" if transport == "streamable-http" else "/sse"
        mcp_url = remote_url.rstrip("/") + path
        entry = {
            "type": "http" if transport == "streamable-http" else "sse",
            "url": mcp_url,
        }
        if api_key:
            entry["headers"] = {"Authorization": f"Bearer {api_key}"}
        mode = f"remote {transport} ({mcp_url})"
    else:
        entry = {
            "command": argus_bin,
            "args": ["mcp", "serve"],
            "description": "Argus search broker",
        }
        if local_execution_mode == "standalone":
            entry["env"] = {"ARGUS_MCP_STANDALONE": "true"}
        mode = "local stdio"

    if remote_url:
        opencode_entry = {
            "type": "remote",
            "url": entry["url"],
            "enabled": True,
        }
        if api_key:
            opencode_entry["headers"] = {"Authorization": f"Bearer {api_key}"}
    else:
        opencode_entry = {
            "type": "local",
            "command": [argus_bin, "mcp", "serve"],
            "enabled": True,
            "timeout": 10000,
        }
        if local_execution_mode == "standalone":
            opencode_entry["environment"] = {"ARGUS_MCP_STANDALONE": "true"}

    write_json = client in ("all", "claude", "opencode")

    if write_json:
        # 1. Claude Code (global ~/.claude.json or project .mcp.json)
        # 2. Cursor (global ~/.cursor/mcp.json or project .cursor/mcp.json)
        paths = []
        if client in ("all", "claude"):
            if global_:
                paths.append(Path.home() / ".claude.json")
                paths.append(Path.home() / ".cursor" / "mcp.json")
                # Claude Desktop (macOS and Linux)
                if sys.platform == "darwin":
                    paths.append(
                        Path.home()
                        / "Library"
                        / "Application Support"
                        / "Claude"
                        / "claude_desktop_config.json"
                    )
                else:
                    paths.append(
                        Path.home()
                        / ".config"
                        / "Claude"
                        / "claude_desktop_config.json"
                    )
                scope_name = "global"
            else:
                paths.append(Path(".mcp.json"))
                paths.append(Path(".cursor") / "mcp.json")
                scope_name = "project"
        else:
            scope_name = "global" if global_ else "project"

        updated_paths = []
        for config_path in paths:
            if global_ and not config_path.parent.exists():
                continue
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
                data = (
                    json.loads(config_path.read_text())
                    if config_path.stat().st_size
                    else {}
                )
            except json.JSONDecodeError:
                data = {}

            servers = data.setdefault("mcpServers", {})
            if "argus" in servers and servers["argus"] == entry:
                updated_paths.append(str(config_path))
                continue

            if "argus" in servers:
                if not click.confirm(
                    f"argus MCP config already exists in {config_path}. Overwrite?",
                    default=False,
                ):
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

    if client in ("all", "opencode"):
        opencode_path = (
            Path.home() / ".config" / "opencode" / "config.json"
            if global_
            else Path(".opencode") / "opencode.json"
        )
        if global_ and not opencode_path.parent.exists():
            click.echo(
                "\nOpenCode — ~/.config/opencode/ not found; is OpenCode installed?"
            )
        else:
            opencode_path.parent.mkdir(parents=True, exist_ok=True)
            opencode_path.touch(mode=0o644, exist_ok=True)
            try:
                data = (
                    json.loads(opencode_path.read_text())
                    if opencode_path.stat().st_size
                    else {}
                )
            except json.JSONDecodeError:
                data = {}

            servers = data.setdefault("mcp", {})
            if "argus" in servers and servers["argus"] != opencode_entry:
                if not click.confirm(
                    f"argus MCP config already exists in {opencode_path}. Overwrite?",
                    default=False,
                ):
                    servers = None

            if servers is not None:
                servers["argus"] = opencode_entry
                opencode_path.write_text(json.dumps(data, indent=2) + "\n")
                click.echo(
                    f"\nOpenCode — updated {opencode_path} with argus MCP ({mode})"
                )

    if client in ("all", "gemini"):
        click.echo("\nGemini CLI — run once to register:")
        if remote_url:
            path = "/mcp" if transport == "streamable-http" else "/sse"
            mcp_url = remote_url.rstrip("/") + path
            t_flag = "http" if transport == "streamable-http" else "sse"
            if api_key:
                click.echo(
                    f'  gemini mcp add argus {mcp_url} -t {t_flag} -H "Authorization: Bearer {api_key}"'
                )
            else:
                click.echo(f"  gemini mcp add argus {mcp_url} -t {t_flag}")
        else:
            prefix = (
                "env ARGUS_MCP_STANDALONE=true "
                if local_execution_mode == "standalone"
                else ""
            )
            click.echo(f"  gemini mcp add argus {prefix}{argus_bin} mcp serve")

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
                if local_execution_mode == "standalone":
                    new_section += 'env = { ARGUS_MCP_STANDALONE = "true" }\n'

            if "[mcp_servers.argus]" in toml_text:
                # Remove old section (everything from [mcp_servers.argus] to next [section])
                import re

                toml_text = re.sub(
                    r"(?ms)\n\[mcp_servers\.argus\].*?(?=\n\[|\Z)",
                    new_section,
                    toml_text,
                )
            else:
                toml_text = toml_text.rstrip("\n") + new_section

            toml_path.write_text(toml_text)
            click.echo(
                f"\nCodex — updated ~/.codex/config.toml with argus MCP ({remote_url or 'local stdio'})"
            )

            # Ensure ARGUS_API_KEY is exported in shell profile (Codex reads it as env var)
            if api_key and remote_url:
                zshrc = Path.home() / ".zshrc"
                bashrc = Path.home() / ".bashrc"
                rc_path = zshrc if zshrc.exists() else bashrc
                rc_text = rc_path.read_text() if rc_path.exists() else ""
                if "ARGUS_API_KEY" not in rc_text:
                    with rc_path.open("a") as f:
                        f.write(
                            f"\n# Argus MCP bearer token\nexport ARGUS_API_KEY={api_key}\n"
                        )
                    click.echo(
                        f"  Added ARGUS_API_KEY to {rc_path.name} (run: source ~/{rc_path.name})"
                    )
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
        import mcp.server  # noqa: F401

        checks.append(("MCP package", True, "installed"))
    except ImportError:
        checks.append(("MCP package", False, "pip install 'argus-search[mcp]'"))

    # 2. MCPServer Context (for progress notifications)
    try:
        from mcp.server.mcpserver.context import Context  # noqa: F401

        checks.append(("Progress notifications", True, "Context available"))
    except Exception:
        checks.append(
            ("Progress notifications", False, "MCP version may not support Context")
        )

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
    checks.append(
        (
            "MCP config file",
            has_argus,
            f"found in {p}" if has_argus else "run 'argus mcp init'",
        )
    )

    # 4. HTTP execution authority (or explicit development standalone)
    from argus.authority import (
        AuthorityConfigurationError,
        adapter_execution_mode,
        authority_client_config,
    )

    adapter_mode = adapter_execution_mode()
    if adapter_mode == "http":
        try:
            config = authority_client_config(adapter="mcp")
            checks.append(("HTTP execution authority", True, config.base_url))
        except AuthorityConfigurationError as exc:
            checks.append(("HTTP execution authority", False, str(exc)))
    elif adapter_mode == "standalone":
        checks.append(
            (
                "HTTP execution authority",
                True,
                "explicit development standalone",
            )
        )
    else:
        checks.append(
            (
                "HTTP execution authority",
                False,
                "set ARGUS_AUTHORITY_URL and ARGUS_AUTHORITY_TOKEN",
            )
        )

    # 5. Listener key for remote MCP transport
    from argus.auth import AuthConfig

    auth = AuthConfig.from_env()
    checks.append(
        (
            "ARGUS_API_KEY (remote MCP)",
            auth.has_caller_key(),
            "set" if auth.has_caller_key() else "needed only for remote transport",
        )
    )

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


@cli.group()
def ledger():
    """Manage the durable search ledger."""
    pass


@ledger.command(name="reconcile-legacy")
@click.option("--source", required=True, help="Legacy SQLAlchemy database URL")
@click.option(
    "--target",
    default=None,
    help="Ledger SQLAlchemy database URL (defaults to ARGUS_DB_URL)",
)
@click.option(
    "--apply",
    is_flag=True,
    help="Apply imports; without this flag the command only reports changes",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def reconcile_legacy(source, target, apply, as_json):
    """Report or import legacy search runs. Defaults to a non-mutating dry run."""
    from argus.legacy_cli_ledger import reconcile_legacy_cli

    report = reconcile_legacy_cli(source, target, apply)
    if as_json:
        _emit_json(report)
        return
    click.echo("Legacy search reconciliation:")
    for key in ("source", "imported", "skipped", "conflicting"):
        click.echo(f"  {key}: {report[key]}")
    if not apply:
        click.echo("Dry run only; no target mutation was performed.")


@ledger.command(name="reconcile-sessions")
@click.option("--source", required=True, help="Legacy session SQLite database URL")
@click.option(
    "--target",
    default=None,
    help="Ledger SQLAlchemy database URL (defaults to ARGUS_DB_URL)",
)
@click.option(
    "--apply",
    is_flag=True,
    help="Apply imports; without this flag the command only reports changes",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def reconcile_sessions(source, target, apply, as_json):
    """Report or import legacy session history. Dry-run is the default."""
    from argus.legacy_cli_ledger import reconcile_sessions_cli

    report = reconcile_sessions_cli(source, target, apply)
    if as_json:
        _emit_json(report)
        return
    click.echo("Legacy session reconciliation:")
    for key in ("source", "imported", "skipped", "conflicting"):
        click.echo(f"  {key}: {report[key]}")
    if not apply:
        click.echo("Dry run only; no target mutation was performed.")


@corpus.command(name="import-docs-cache")
@click.option(
    "--source",
    "-s",
    required=True,
    type=click.Path(exists=True),
    help="Path to legacy docs-cache root",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def import_docs_cache(source, as_json):
    """Import a legacy docs-cache tree into Argus-owned storage."""
    from argus import standalone_cli

    standalone_cli.require_nonproduction()
    return standalone_cli.import_docs_cache(source=source, as_json=as_json)


@cli.group()
def cookies():
    """Manage browser cookies for authenticated extraction."""
    if os.environ.get("ARGUS_ENV", "development").strip().lower() == "production":
        raise click.ClickException(
            "Production cookie operations are reserved for the "
            "HTTP API execution authority"
        )
    from argus import standalone_cli

    standalone_cli.require_nonproduction()


@cookies.command(name="import")
@click.option(
    "--domain",
    "-d",
    default=None,
    help="Domain (e.g. nytimes.com). Inferred from cookies if omitted.",
)
@click.option(
    "--file",
    "-f",
    "filepath",
    default=None,
    type=click.Path(exists=True),
    help="EditThisCookie JSON file. If omitted, imports all from inbox.",
)
def cookies_import(domain, filepath):
    """Import cookies through the standalone compatibility boundary."""
    from argus import standalone_cli

    return standalone_cli.cookies_import(domain=domain, filepath=filepath)


@cookies.command(name="health")
def cookies_health():
    """Show standalone cookie-domain health."""
    from argus import standalone_cli

    return standalone_cli.cookies_health()
