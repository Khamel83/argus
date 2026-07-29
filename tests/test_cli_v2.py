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
