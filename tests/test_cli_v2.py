"""Exact negotiated HTTP behaviour for the evidence-aware CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
import pytest
from click.testing import CliRunner


def _envelope(outcome: str = "success") -> dict[str, object]:
    successful = outcome in {"success", "degraded", "empty"}
    return {
        "contract_version": "2.0",
        "outcome": outcome,
        "request_id": "cli-request-1",
        "result": {
            "query": "negotiated query",
            "mode": "discovery",
            "results": [
                {
                    "title": "Canonical result",
                    "url": "https://example.com/result",
                    "snippet": "evidence",
                }
            ],
            "total_results": 1,
            "cached": False,
        }
        if successful
        else None,
        "error": None
        if successful
        else {
            "code": outcome,
            "detail": "Safe failure",
            "status": 503,
        },
    }


@dataclass(frozen=True)
class _Selection:
    contract_version: str | None
    base_path: str | None
    outcome: str


class _Authority:
    def __init__(self, selection: _Selection, response: dict[str, object]):
        self.selection = selection
        self.response = response
        self.calls: list[tuple[str, object]] = []

    async def resolve_http_contract(self, deployment_id, clock, *, refresh=False):
        self.calls.append(("discover", (deployment_id, refresh, clock())))
        return self.selection

    async def request_v2(self, path, *, payload, token=None):
        self.calls.append(("post-v2", (path, payload, token)))
        return self.response

    async def search(self, payload, *, token=None):
        self.calls.append(("post-v1", (payload, token)))
        return self.response

    async def request(self, method, path, *, payload=None, token=None):
        self.calls.append(("post-v1", (method, path, payload, token)))
        return self.response


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.yielded = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self):
        return None


def _invoke(monkeypatch, authority: _Authority, arguments: list[str]):
    from argus.cli import main as cli_main

    monkeypatch.setattr(cli_main, "_http_authority_client", lambda: authority)
    return CliRunner().invoke(cli_main.cli, arguments)


def test_search_prefers_v2_and_json_writes_the_exact_envelope(monkeypatch):
    envelope = _envelope()
    authority = _Authority(_Selection("2.0", "/api/v2", "ready"), envelope)

    result = _invoke(
        monkeypatch, authority, ["search", "-q", "negotiated query", "--json"]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout) == envelope
    assert [kind for kind, _ in authority.calls] == ["discover", "post-v2"]
    assert authority.calls[1] == (
        "post-v2",
        (
            "/api/v2/search",
            {
                "query": "negotiated query",
                "mode": "discovery",
                "max_results": 10,
                "include_attribution": False,
                "free_only": False,
                "caller": "cli",
            },
            None,
        ),
    )


def test_search_uses_v1_only_after_legacy_discovery(monkeypatch):
    legacy = {
        "query": "legacy",
        "mode": "discovery",
        "results": [],
        "total_results": 0,
        "cached": False,
        "search_run_id": "legacy-run",
    }
    authority = _Authority(_Selection("1", "/api", "ready"), legacy)

    result = _invoke(monkeypatch, authority, ["search", "-q", "legacy"])

    assert result.exit_code == 0, result.output
    assert [kind for kind, _ in authority.calls] == ["discover", "post-v1"]
    assert "Query: legacy" in result.stdout


def test_search_does_not_execute_after_unready_discovery(monkeypatch):
    authority = _Authority(_Selection(None, None, "unready"), _envelope())

    result = _invoke(monkeypatch, authority, ["search", "-q", "never", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["outcome"] == "unready"
    assert "contract discovery is unavailable" in result.stderr
    assert [kind for kind, _ in authority.calls] == ["discover"]


@pytest.mark.parametrize("outcome", ["success", "degraded", "empty"])
def test_v2_accepted_outcomes_exit_zero(monkeypatch, outcome):
    authority = _Authority(
        _Selection("2.0", "/api/v2", "ready"),
        _envelope(outcome),
    )

    result = _invoke(monkeypatch, authority, ["search", "-q", "accepted", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["outcome"] == outcome


def test_v2_canonical_failure_exits_one_and_keeps_json_stdout_exact(monkeypatch):
    envelope = _envelope("providers_failed")
    authority = _Authority(_Selection("2.0", "/api/v2", "ready"), envelope)

    result = _invoke(monkeypatch, authority, ["search", "-q", "failed", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == envelope
    assert "providers_failed" in result.stderr
    assert [kind for kind, _ in authority.calls] == ["discover", "post-v2"]


def test_human_v2_output_keeps_results_and_adds_evidence_labels(monkeypatch):
    authority = _Authority(_Selection("2.0", "/api/v2", "ready"), _envelope())

    result = _invoke(monkeypatch, authority, ["search", "-q", "negotiated query"])

    assert result.exit_code == 0, result.output
    assert "Query: negotiated query" in result.stdout
    assert "Outcome: success" in result.stdout
    assert "Request ID: cli-request-1" in result.stdout
    assert "Evidence:" in result.stdout


def test_extract_prefers_v2_and_json_writes_the_exact_envelope(monkeypatch):
    envelope = _envelope()
    envelope["result"] = {
        "url": "https://example.com/article",
        "title": "Extracted title",
        "text": "Extracted evidence",
        "word_count": 2,
        "extractor": "trafilatura",
    }
    authority = _Authority(_Selection("2.0", "/api/v2", "ready"), envelope)

    result = _invoke(
        monkeypatch,
        authority,
        ["extract", "-u", "https://example.com/article", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == envelope
    assert [kind for kind, _ in authority.calls] == ["discover", "post-v2"]
    assert authority.calls[1] == (
        "post-v2",
        (
            "/api/v2/extract",
            {
                "url": "https://example.com/article",
                "domain": None,
                "mode": "default",
                "caller": "cli",
            },
            None,
        ),
    )


def test_recover_url_prefers_v2_and_json_writes_the_exact_envelope(monkeypatch):
    envelope = _envelope()
    envelope["result"] = {
        "query": "https://example.com/gone",
        "results": [{"title": "Recovered", "url": "https://archive.example/article"}],
        "total_results": 1,
    }
    authority = _Authority(_Selection("2.0", "/api/v2", "ready"), envelope)

    result = _invoke(
        monkeypatch,
        authority,
        ["recover-url", "-u", "https://example.com/gone", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == envelope
    assert [kind for kind, _ in authority.calls] == ["discover", "post-v2"]
    assert authority.calls[1] == (
        "post-v2",
        (
            "/api/v2/recover-url",
            {
                "url": "https://example.com/gone",
                "title": None,
                "domain": None,
            },
            None,
        ),
    )


def test_click_usage_errors_remain_exit_two(monkeypatch):
    authority = _Authority(_Selection("2.0", "/api/v2", "ready"), _envelope())

    result = _invoke(monkeypatch, authority, ["search"])

    assert result.exit_code == 2
    assert "Missing option '--query'" in result.output
    assert authority.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-1, 0])
async def test_cli_uses_shared_reader_at_and_below_11_mib(offset):
    from argus.authority import AuthorityClientConfig, HttpAuthorityClient

    bound = 11 * 1024 * 1024
    envelope = _envelope()
    envelope["result"] = {"padding": ""}
    encoded = json.dumps(envelope, separators=(",", ":")).encode()
    envelope["result"]["padding"] = "x" * (bound + offset - len(encoded))
    encoded = json.dumps(envelope, separators=(",", ":")).encode()
    assert len(encoded) == bound + offset
    stream = _ChunkStream([encoded[:1024], encoded[1024:]])

    async def handler(_request):
        return httpx.Response(200, stream=stream)

    client = HttpAuthorityClient(
        AuthorityClientConfig("https://authority.example", "token"),
        transport=httpx.MockTransport(handler),
    )

    assert (
        await client.request_v2("/api/v2/search", payload={"query": "bounded"})
        == envelope
    )
    assert stream.yielded == 2


@pytest.mark.asyncio
async def test_cli_shared_reader_stops_one_byte_over_11_mib_before_parse():
    from argus.authority import (
        AuthorityClientConfig,
        AuthorityRequestError,
        HttpAuthorityClient,
    )

    bound = 11 * 1024 * 1024
    stream = _ChunkStream([b"{" + (b"x" * (bound - 1)), b"x", b"unread"])

    async def handler(_request):
        return httpx.Response(200, stream=stream)

    client = HttpAuthorityClient(
        AuthorityClientConfig("https://authority.example", "token"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(AuthorityRequestError, match="size limit"):
        await client.request_v2("/api/v2/search", payload={"query": "bounded"})
    assert stream.yielded == 2


_RETRIEVAL_CASES = (
    ("search", ["search", "-q", "actual"], "/api/v2/search", "/api/search"),
    (
        "extract",
        ["extract", "-u", "https://example.com/article"],
        "/api/v2/extract",
        "/api/extract",
    ),
    (
        "recover-url",
        ["recover-url", "-u", "https://example.com/gone"],
        "/api/v2/recover-url",
        "/api/recover-url",
    ),
)


def _capabilities(*, v2: bool) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "1.0",
        "execution_authority": "http-api",
        "role": "primary",
        "capabilities": {
            "search": True,
            "extraction": True,
            "recovery": True,
            "expansion": True,
        },
    }
    if v2:
        document["http_contracts"] = [
            {"version": "1", "base_path": "/api", "legacy": True},
            {"version": "2.0", "base_path": "/api/v2", "legacy": False},
        ]
    return document


def _invoke_real_http(monkeypatch, arguments, handler):
    import argus.authority as authority_module

    real = authority_module.HttpAuthorityClient(
        authority_module.AuthorityClientConfig("https://authority.example", "token"),
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setenv("ARGUS_ENV", "development")
    monkeypatch.setenv("ARGUS_AUTHORITY_URL", "https://authority.example")
    monkeypatch.setenv("ARGUS_AUTHORITY_TOKEN", "token")
    monkeypatch.setattr(authority_module, "HttpAuthorityClient", lambda _config: real)
    from argus.cli.main import cli

    return CliRunner().invoke(cli, arguments)


def _route_result(command: str) -> dict[str, object]:
    if command == "extract":
        return {
            "url": "https://example.com/article",
            "title": "Article",
            "text": "Evidence text",
            "word_count": 2,
            "extractor": "trafilatura",
        }
    if command == "recover-url":
        return {
            "query": "https://example.com/gone",
            "results": [{"title": "Recovered", "url": "https://archive.example"}],
            "total_results": 1,
        }
    return _envelope()["result"]


def _actual_envelope(command: str, outcome: str) -> dict[str, object]:
    envelope = _envelope(outcome)
    if outcome in {"success", "degraded", "empty"}:
        envelope["result"] = _route_result(command)
    return envelope


def _expected_human(command: str, envelope: dict[str, object]) -> str:
    result = envelope["result"] or {}
    outcome = envelope["outcome"]
    prefix = f"Outcome: {outcome}\nRequest ID: cli-request-1\n"
    if command == "extract":
        if not result:
            return (
                "Words: 0 | Extractor: unknown\n"
                + prefix
                + "Evidence: unavailable\n\n\n"
            )
        return (
            f"Title: {result['title']}\n"
            f"Words: {result.get('word_count', 0)} | "
            f"Extractor: {result.get('extractor') or 'unknown'}\n"
            + prefix
            + "Evidence: available\n\n"
            + (result.get("text") or "")
            + "\n"
        )
    if command == "recover-url":
        lines = [
            "Recovery for: https://example.com/gone",
            f"Results: {result.get('total_results', 0)}",
            f"Outcome: {outcome}",
            "Request ID: cli-request-1",
            "Evidence: " + ("available" if result else "unavailable"),
        ]
        for item in result.get("results") or []:
            lines.extend(
                [f"  1. {item.get('title', '')}", f"     {item.get('url', '')}"]
            )
        return "\n".join(lines) + "\n"
    lines = [
        f"Query: {result.get('query', 'actual')}",
        f"Mode: {result.get('mode', 'discovery')} | Results: {result.get('total_results', 0)} | Cached: {result.get('cached', False)}",
        f"Run ID: {result.get('search_run_id')}",
        f"Outcome: {outcome}",
        "Request ID: cli-request-1",
        "Evidence: " + ("available" if result else "unavailable"),
        "",
    ]
    for item in result.get("results") or []:
        lines.extend([f"  1. {item.get('title', '')}", f"     {item.get('url', '')}"])
        if item.get("snippet"):
            lines.append(f"     {item['snippet'][:120]}")
        lines.append("")
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    ("command", "arguments", "v2_path", "_v1_path"), _RETRIEVAL_CASES
)
@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "degraded",
        "empty",
        "invalid_request",
        "authentication_rejected",
        "policy_rejected",
        "timeout",
        "persistence_failed",
        "providers_failed",
        "extraction_failed",
        "unready",
    ],
)
def test_real_discovery_v2_has_exact_json_and_one_selected_post(
    monkeypatch, command, arguments, v2_path, _v1_path, outcome
):
    envelope = _actual_envelope(command, outcome)
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=_capabilities(v2=True))
        assert request.url.path == v2_path
        return httpx.Response(200, json=envelope)

    result = _invoke_real_http(monkeypatch, [*arguments, "--json"], handler)

    assert result.exit_code == (0 if outcome in {"success", "degraded", "empty"} else 1)
    assert result.stdout == json.dumps(envelope, indent=2) + "\n"
    expected_stderr = (
        ""
        if outcome in {"success", "degraded", "empty"}
        else (f"Argus operation failed ({outcome}): Safe failure\n")
    )
    assert result.stderr == expected_stderr
    assert requests == [("GET", "/api/capabilities"), ("POST", v2_path)]


@pytest.mark.parametrize(
    ("command", "arguments", "_v2_path", "v1_path"), _RETRIEVAL_CASES
)
def test_real_legacy_discovery_preserves_frozen_json(
    monkeypatch, command, arguments, _v2_path, v1_path
):
    legacy = _route_result(command)
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=_capabilities(v2=False))
        return httpx.Response(200, json=legacy)

    result = _invoke_real_http(monkeypatch, [*arguments, "--json"], handler)

    expected = dict(legacy)
    if command == "search":
        expected["run_id"] = expected.pop("search_run_id", None)
    elif command == "recover-url":
        expected = {"url": "https://example.com/gone", "results": legacy["results"]}
    assert result.exit_code == 0
    assert result.stdout == json.dumps(expected, indent=2) + "\n"
    assert result.stderr == ""
    assert requests == [("GET", "/api/capabilities"), ("POST", v1_path)]


@pytest.mark.parametrize("capability_body", [b"{", b"[]"])
def test_real_malformed_discovery_never_posts(monkeypatch, capability_body):
    requests = []
    monkeypatch.setattr("argus.cli.main.secrets.token_hex", lambda _n: "deadbeef")

    def handler(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(200, content=capability_body)

    result = _invoke_real_http(
        monkeypatch, ["search", "-q", "blocked", "--json"], handler
    )

    expected = {
        "contract_version": "2.0",
        "outcome": "unready",
        "request_id": "cli-deadbeef",
        "result": None,
        "error": {
            "type": "urn:argus:problem:unready",
            "title": "Unready",
            "status": 503,
            "detail": "Argus HTTP contract discovery is unavailable",
            "instance": "urn:argus:request:cli-deadbeef",
            "code": "unready",
            "retryable": False,
            "retry_after_seconds": None,
        },
    }
    assert result.exit_code == 1
    assert result.stdout == json.dumps(expected, indent=2) + "\n"
    assert (
        result.stderr
        == "Argus operation failed (unready): Argus HTTP contract discovery is unavailable\n"
    )
    assert requests == [("GET", "/api/capabilities")]


def test_real_v2_execution_exception_never_retries_v1(monkeypatch):
    requests = []
    monkeypatch.setattr("argus.cli.main.secrets.token_hex", lambda _n: "deadbeef")

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=_capabilities(v2=True))
        raise httpx.ReadError("authority disconnected", request=request)

    result = _invoke_real_http(monkeypatch, ["search", "-q", "once", "--json"], handler)

    assert result.exit_code == 1
    assert (
        result.stderr
        == "Argus operation failed (unready): Argus HTTP execution authority is unavailable\n"
    )
    assert requests == [("GET", "/api/capabilities"), ("POST", "/api/v2/search")]


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_actual_cli_enforces_the_11_mib_response_bound(monkeypatch, offset):
    bound = 11 * 1024 * 1024
    envelope = _actual_envelope("search", "success")
    envelope["result"]["padding"] = ""
    encoded = json.dumps(envelope, separators=(",", ":")).encode()
    envelope["result"]["padding"] = "x" * (bound + offset - len(encoded))
    encoded = json.dumps(envelope, separators=(",", ":")).encode()
    stream = _ChunkStream([encoded[:1024], encoded[1024:]])
    requests = []
    monkeypatch.setattr("argus.cli.main.secrets.token_hex", lambda _n: "deadbeef")

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=_capabilities(v2=True))
        return httpx.Response(200, stream=stream)

    result = _invoke_real_http(
        monkeypatch, ["search", "-q", "bounded", "--json"], handler
    )

    assert requests == [("GET", "/api/capabilities"), ("POST", "/api/v2/search")]
    if offset <= 0:
        assert result.exit_code == 0
        assert result.stdout == json.dumps(envelope, indent=2) + "\n"
        assert result.stderr == ""
    else:
        assert result.exit_code == 1
        assert result.stdout.count("\n") > 1
        assert (
            result.stderr
            == "Argus operation failed (unready): Argus HTTP execution authority is unavailable\n"
        )


@pytest.mark.parametrize(
    ("command", "arguments", "_v2_path", "_v1_path"), _RETRIEVAL_CASES
)
@pytest.mark.parametrize(
    "outcome", ["success", "degraded", "empty", "providers_failed"]
)
def test_human_v2_output_is_exact_for_retrieval_commands(
    monkeypatch, command, arguments, _v2_path, _v1_path, outcome
):
    envelope = _actual_envelope(command, outcome)

    def handler(request):
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=_capabilities(v2=True))
        return httpx.Response(200, json=envelope)

    result = _invoke_real_http(monkeypatch, arguments, handler)

    assert result.exit_code == (0 if outcome in {"success", "degraded", "empty"} else 1)
    assert result.stdout == _expected_human(command, envelope)
    assert result.stderr == (
        ""
        if outcome in {"success", "degraded", "empty"}
        else f"Argus operation failed ({outcome}): Safe failure\n"
    )
