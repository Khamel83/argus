"""Focused parity tests for the HTTP, MCP, and CLI extraction transports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner


def _success_envelope(result: dict | None = None) -> dict:
    return {
        "contract_version": "2.0",
        "outcome": "success",
        "request_id": "transport-request-1",
        "result": result or {"accepted": True},
        "error": None,
    }


def _failure_envelope(outcome: str = "providers_failed") -> dict:
    status = {
        "providers_failed": 502,
        "extraction_failed": 502,
        "unready": 503,
    }[outcome]
    return {
        "contract_version": "2.0",
        "outcome": outcome,
        "request_id": "transport-failure-1",
        "result": None,
        "error": {
            "type": f"urn:argus:problem:{outcome}",
            "title": outcome.replace("_", " ").title(),
            "status": status,
            "detail": "Typed authority failure",
            "instance": "urn:argus:request:transport-failure-1",
            "code": outcome,
            "retryable": False,
            "retry_after_seconds": None,
        },
    }


def test_extract_request_normalizes_the_complete_transport_input():
    from argus.api.schemas import ExtractRequest

    request = ExtractRequest(
        url="https://example.com/article",
        mode="archive_ingest",
        content_type="webpage",
        free_only=True,
        caller="maya-intake",
    )

    assert request.model_dump() == {
        "url": "https://example.com/article",
        "domain": None,
        "mode": "archive_ingest",
        "content_type": "webpage",
        "free_only": True,
        "caller": "maya-intake",
    }


@pytest.mark.asyncio
async def test_accepted_extraction_forwards_profile_and_identity_without_label_spoofing():
    from argus.extraction.models import ExtractedContent, ExtractorName
    from argus.operations.accepted import AcceptedOperationService

    seen = {}

    async def extractor(url, **kwargs):
        seen.update(kwargs)
        return ExtractedContent(
            url=url,
            title="Accepted",
            text="accepted extraction content",
            word_count=3,
            extractor=ExtractorName.TRAFILATURA,
        )

    service = AcceptedOperationService(
        broker_provider=lambda: MagicMock(),
        repository_provider=lambda: MagicMock(),
        extractor=extractor,
    )
    operation = await service.extract(
        SimpleNamespace(
            url="https://example.com/article",
            domain="example.com",
            mode="archive_ingest",
            content_type="webpage",
            free_only=True,
            caller="display-label",
        ),
        principal="authenticated-principal",
        request_id="transport-request-2",
    )

    assert operation.outcome.value == "success"
    assert seen["mode"] == "archive_ingest"
    assert seen["content_type"] == "webpage"
    assert seen["free_only"] is True
    assert seen["caller"] == "authenticated-principal"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_outcome"),
    (
        (
            "contract",
            "extraction_failed",
        ),
        (
            "persistence",
            "persistence_failed",
        ),
        (
            "conflict",
            "invalid_request",
        ),
        (
            "preflight",
            "authentication_rejected",
        ),
    ),
)
async def test_accepted_extraction_preserves_typed_failure_outcomes(
    failure,
    expected_outcome,
):
    from argus.contracts import CanonicalOutcome
    from argus.operations.accepted import AcceptedOperationService
    from argus.extraction.outcomes import (
        ExtractionAcceptanceConflict,
        ExtractionContractRejected,
        ExtractionPersistenceFailed,
        ExtractionPreflightRejected,
    )

    failures = {
        "contract": ExtractionContractRejected(),
        "persistence": ExtractionPersistenceFailed(),
        "conflict": ExtractionAcceptanceConflict(),
        "preflight": ExtractionPreflightRejected(
            CanonicalOutcome.AUTHENTICATION_REJECTED
        ),
    }

    async def extractor(_url, **_kwargs):
        raise failures[failure]

    operation = await AcceptedOperationService(
        broker_provider=lambda: MagicMock(),
        repository_provider=lambda: MagicMock(),
        extractor=extractor,
    ).extract(
        SimpleNamespace(
            url="https://example.com/article",
            domain=None,
            mode="default",
            content_type="article",
            free_only=False,
        ),
        principal="authenticated-principal",
        request_id="transport-failure-request",
    )

    assert operation.outcome.value == expected_outcome
    assert operation.error.code == expected_outcome


@pytest.mark.asyncio
async def test_mcp_v2_extraction_forwards_the_complete_input():
    from argus.mcp.http_adapter import HttpMcpAdapter

    @dataclass
    class Selection:
        contract_version: str
        base_path: str
        outcome: str

    class Client:
        def __init__(self):
            self.calls = []

        async def resolve_http_contract(self, deployment_id, clock, *, refresh=False):
            del deployment_id, clock, refresh
            return Selection("2.0", "/api/v2", "ready")

        async def request_v2(self, path, *, payload, token=None):
            self.calls.append((path, payload, token))
            return _success_envelope()

    client = Client()
    result = await HttpMcpAdapter(client).extract_content_v2(
        "https://example.com/article",
        "example.com",
        mode="archive_ingest",
        content_type="webpage",
        free_only=True,
        caller_label="maya-intake",
        caller_identity="authenticated-principal",
        token="scoped-token",
    )

    assert result == _success_envelope()
    assert client.calls == [
        (
            "/api/v2/extract",
            {
                "url": "https://example.com/article",
                "domain": "example.com",
                "mode": "archive_ingest",
                "content_type": "webpage",
                "free_only": True,
                "caller": "maya-intake",
            },
            "scoped-token",
        )
    ]


@pytest.mark.asyncio
async def test_mcp_v2_adapter_preserves_typed_authority_failure():
    from argus.authority import AuthorityRequestError
    from argus.mcp.http_adapter import HttpMcpAdapter

    class Client:
        async def resolve_http_contract(self, deployment_id, clock, *, refresh=False):
            del deployment_id, clock, refresh
            return SimpleNamespace(
                contract_version="2.0", base_path="/api/v2", outcome="ready"
            )

        async def request_v2(self, path, *, payload, token=None):
            del path, payload, token
            error = AuthorityRequestError(
                "typed authority response",
                status_code=502,
            )
            error.envelope = _failure_envelope()
            raise error

    result = await HttpMcpAdapter(Client()).search_web_v2(query="typed")

    assert result == _failure_envelope()


def test_cli_retrieval_fails_closed_without_explicit_standalone(monkeypatch):
    from argus.cli import main as cli_main

    monkeypatch.delenv("ARGUS_AUTHORITY_URL", raising=False)
    monkeypatch.delenv("ARGUS_AUTHORITY_TOKEN", raising=False)
    monkeypatch.setenv("ARGUS_ENV", "development")
    monkeypatch.delenv("ARGUS_MCP_STANDALONE", raising=False)
    monkeypatch.setattr(
        "argus.standalone_cli.search",
        lambda **_kwargs: pytest.fail("local broker fallback is forbidden"),
    )

    result = CliRunner().invoke(cli_main.cli, ["search", "-q", "closed"])

    assert result.exit_code != 0
    assert "ARGUS_AUTHORITY_URL" in result.output


def test_cli_standalone_retrieval_requires_and_honors_explicit_opt_in(monkeypatch):
    from argus.cli import main as cli_main

    monkeypatch.delenv("ARGUS_AUTHORITY_URL", raising=False)
    monkeypatch.delenv("ARGUS_AUTHORITY_TOKEN", raising=False)
    monkeypatch.setenv("ARGUS_ENV", "development")
    monkeypatch.setenv("ARGUS_MCP_STANDALONE", "true")
    seen = {}

    def standalone(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr("argus.standalone_cli.search", standalone)
    result = CliRunner().invoke(cli_main.cli, ["search", "-q", "standalone"])

    assert result.exit_code == 0, result.output
    assert seen["query"] == "standalone"


def test_cli_extract_forwards_the_complete_transport_input(monkeypatch):
    from argus.cli import main as cli_main

    @dataclass(frozen=True)
    class Selection:
        contract_version: str
        base_path: str
        outcome: str

    class Authority:
        def __init__(self):
            self.calls = []

        async def resolve_http_contract(self, deployment_id, clock, *, refresh=False):
            del deployment_id, clock, refresh
            return Selection("2.0", "/api/v2", "ready")

        async def request_v2(self, path, *, payload, token=None):
            self.calls.append((path, payload, token))
            return _success_envelope(
                {
                    "url": payload["url"],
                    "title": "Extracted",
                    "text": "content",
                    "word_count": 1,
                    "extractor": "trafilatura",
                }
            )

    authority = Authority()
    monkeypatch.setattr(cli_main, "_http_authority_client", lambda: authority)
    result = CliRunner().invoke(
        cli_main.cli,
        [
            "extract",
            "--url",
            "https://example.com/article",
            "--domain",
            "example.com",
            "--mode",
            "archive_ingest",
            "--content-type",
            "webpage",
            "--free-only",
            "--caller",
            "maya-intake",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["request_id"] == "transport-request-1"
    assert authority.calls == [
        (
            "/api/v2/extract",
            {
                "url": "https://example.com/article",
                "domain": "example.com",
                "mode": "archive_ingest",
                "content_type": "webpage",
                "free_only": True,
                "caller": "maya-intake",
            },
            None,
        )
    ]
