"""Regression tests for every spend and scoped-identity entry point."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from argus.models import ProviderName, SearchResponse


def _empty_response(query):
    return SearchResponse(
        query=query.query,
        mode=query.mode,
        results=[],
        search_run_id="boundary-run",
    )


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/recover-url", {"url": "https://dead.example/post"}),
        ("/api/expand", {"query": "related pages"}),
    ],
)
def test_remote_search_variants_use_scoped_credential_identity(
    monkeypatch, path, payload
):
    from fastapi.testclient import TestClient

    from argus.api.main import create_app

    monkeypatch.setenv(
        "ARGUS_CALLER_CREDENTIALS_JSON",
        '{"maya":{"token":"maya-secret"}}',
    )
    broker = MagicMock()
    broker.search = AsyncMock(side_effect=_empty_response)
    broker.budget_tracker = MagicMock()
    client = TestClient(
        create_app(broker=broker),
        client=("203.0.113.10", 50000),
    )

    response = client.post(
        path,
        json=payload,
        headers={"Authorization": "Bearer maya-secret"},
    )

    assert response.status_code == 200
    query = broker.search.await_args.args[0]
    assert query.caller == "maya"


@pytest.mark.parametrize(
    ("path", "method", "payload"),
    [
        (
            "/api/workflows/recover-article",
            "start_recover_article",
            {"url": "https://dead.example/post", "caller": "spoofed"},
        ),
        (
            "/api/workflows/capture-site",
            "start_capture_site",
            {"url": "https://docs.example", "caller": "spoofed"},
        ),
        (
            "/api/workflows/build-research-pack",
            "start_build_research_pack",
            {"topic": "Argus", "caller": "spoofed"},
        ),
        (
            "/api/workflows/search-and-summarize",
            "start_search_and_summarize",
            {"query": "Argus", "caller": "spoofed"},
        ),
    ],
)
def test_http_workflows_receive_credential_principal(
    monkeypatch, path, method, payload
):
    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.workflows.models import WorkflowKind, WorkflowResult, WorkflowStatus

    monkeypatch.setenv(
        "ARGUS_CALLER_CREDENTIALS_JSON",
        '{"maya":{"token":"maya-secret"}}',
    )
    workflow = MagicMock()
    setattr(
        workflow,
        method,
        AsyncMock(
            return_value=WorkflowResult(
                run_id="wf-boundary",
                kind=WorkflowKind.SEARCH_AND_SUMMARIZE,
                status=WorkflowStatus.RUNNING,
                target="target",
            )
        ),
    )
    broker = MagicMock()
    broker.budget_tracker = MagicMock()
    app = create_app(broker=broker)
    app.state.get_workflows = lambda: workflow
    client = TestClient(app, client=("203.0.113.10", 50000))

    response = client.post(
        path,
        json=payload,
        headers={"Authorization": "Bearer maya-secret"},
    )

    assert response.status_code == 200
    kwargs = getattr(workflow, method).await_args.kwargs
    assert kwargs["caller_identity"] == "maya"
    assert kwargs["caller_label"] == "spoofed"


@pytest.mark.asyncio
async def test_mcp_recover_and_expand_propagate_principal():
    from argus.mcp import tools

    broker = MagicMock()
    broker.search = AsyncMock(side_effect=_empty_response)

    await tools.recover_url(
        broker,
        "https://dead.example/post",
        caller_identity="maya",
        caller_label="spoofed",
    )
    await tools.expand_links(
        broker,
        "related",
        caller_identity="maya",
        caller_label="spoofed",
    )

    queries = [call.args[0] for call in broker.search.await_args_list]
    assert [query.caller for query in queries] == ["maya", "maya"]
    assert [query.metadata["caller_label"] for query in queries] == [
        "spoofed",
        "spoofed",
    ]


@pytest.mark.asyncio
async def test_paid_mcp_provider_test_routes_through_broker():
    from argus.mcp.tools import test_provider_mcp

    broker = MagicMock()
    broker.search = AsyncMock(side_effect=_empty_response)

    await test_provider_mcp(
        broker,
        "brave",
        caller_identity="admin",
        caller_label="smoke",
    )

    query = broker.search.await_args.args[0]
    assert query.providers == [ProviderName.BRAVE]
    assert query.caller == "admin"


@pytest.mark.asyncio
async def test_unledgered_paid_helpers_are_explicitly_disabled(monkeypatch):
    from argus.extraction.firecrawl_extractor import extract_firecrawl
    from argus.extraction.valyu_extractor import extract_valyu_contents
    from argus.providers.valyu_answer import valyu_answer

    monkeypatch.setenv("ARGUS_VALYU_API_KEY", "secret")
    monkeypatch.setenv("ARGUS_FIRECRAWL_API_KEY", "secret")

    with patch("httpx.AsyncClient") as client:
        valyu = await extract_valyu_contents("https://example.com")
        firecrawl = await extract_firecrawl("https://example.com")
        answer = await valyu_answer("question")

    assert "durable spend" in valyu.error
    assert "durable spend" in firecrawl.error
    assert "durable spend" in answer.error
    client.assert_not_called()


def test_durable_budget_renderers_include_uncertainty_and_provider_freshness(
    tmp_path,
):
    from argus.mcp.resources import provider_budgets_resource
    from argus.mcp.tools import search_budgets
    from argus.persistence.provider_spend import create_provider_spend_repository

    repository = create_provider_spend_repository(
        f"sqlite:///{tmp_path / 'budget-renderers.db'}",
        create_schema=True,
    )
    reservation = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=10.0,
        caller_identity="maya",
        caller_label="",
        idempotency_key="renderer-attempt",
    )
    observed_at = datetime.now(tz=None)
    repository.record_provider_snapshot(
        provider=ProviderName.BRAVE,
        balance=9.0,
        observed_at=observed_at,
        actor_identity="provider:brave",
        idempotency_key="renderer-snapshot",
        provider_reference="brave-renderer-event",
        related_attempt_id=reservation.attempt_id,
        authoritative_charge=1.0,
    )
    broker = SimpleNamespace(
        spend_repository=repository,
        budget_tracker=SimpleNamespace(
            get_budget_limit=lambda provider: 10.0
        ),
    )

    markdown = search_budgets(broker)
    resource = json.loads(provider_budgets_resource(broker))

    assert "uncertain=1.0" in markdown
    assert "source=provider" in markdown
    assert resource["brave"]["uncertain_charge"] == 1.0
    assert resource["brave"]["provider_snapshot"]["observed_at"] == (
        observed_at.isoformat()
    )


def test_provider_reconciliation_requires_fresh_linked_charge_evidence(tmp_path):
    from argus.persistence.provider_spend import create_provider_spend_repository

    repository = create_provider_spend_repository(
        f"sqlite:///{tmp_path / 'reconciliation-evidence.db'}",
        create_schema=True,
    )
    reservation = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=10.0,
        caller_identity="maya",
        caller_label="",
        idempotency_key="evidence-attempt",
    )

    with pytest.raises(ValueError, match="fresh"):
        repository.record_provider_snapshot(
            provider=ProviderName.BRAVE,
            balance=9.5,
            observed_at=datetime.now(tz=None) - timedelta(hours=1),
            actor_identity="provider:brave",
            idempotency_key="stale-evidence",
            provider_reference="brave-event-1",
            related_attempt_id=reservation.attempt_id,
            authoritative_charge=0.5,
        )

    snapshot = repository.record_provider_snapshot(
        provider=ProviderName.BRAVE,
        balance=9.5,
        observed_at=datetime.now(tz=None),
        actor_identity="provider:brave",
        idempotency_key="fresh-evidence",
        provider_reference="brave-event-2",
        related_attempt_id=reservation.attempt_id,
        authoritative_charge=0.5,
    )
    with pytest.raises(ValueError, match="charge"):
        repository.resolve(
            reservation.attempt_id,
            actual_charge=0.25,
            outcome="charged",
            source="provider",
            actor_identity="provider:brave",
            idempotency_key="mismatched-charge",
            provider_snapshot_id=snapshot.snapshot_id,
        )


def test_provider_reconciliation_api_requires_provider_scoped_credential(
    tmp_path,
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.persistence.provider_spend import create_provider_spend_repository

    monkeypatch.setenv("ARGUS_ADMIN_API_KEY", "admin-secret")
    monkeypatch.setenv(
        "ARGUS_PROVIDER_RECONCILIATION_KEYS_JSON",
        '{"brave":"brave-provider-secret"}',
    )
    repository = create_provider_spend_repository(
        f"sqlite:///{tmp_path / 'reconciliation-auth.db'}",
        create_schema=True,
    )
    reservation = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=10.0,
        caller_identity="maya",
        caller_label="",
        idempotency_key="api-evidence-attempt",
    )
    broker = SimpleNamespace(
        budget_tracker=SimpleNamespace(
            close=lambda: None,
            get_budget_limit=lambda provider: 10.0,
        )
    )
    client = TestClient(create_app(broker=broker, spend_repository=repository))
    payload = {
        "balance": 9.5,
        "observed_at": datetime.now(tz=None).isoformat(),
        "provider_reference": "brave-api-event",
        "related_attempt_id": reservation.attempt_id,
        "authoritative_charge": 0.5,
        "idempotency_key": "api-evidence",
    }

    unauthorized = client.post(
        "/api/admin/provider-spend/brave/snapshots",
        headers={"X-Admin-API-Key": "admin-secret"},
        json=payload,
    )
    authorized = client.post(
        "/api/admin/provider-spend/brave/snapshots",
        headers={
            "X-Admin-API-Key": "admin-secret",
            "X-Provider-Reconciliation-Key": "brave-provider-secret",
        },
        json=payload,
    )
    resolve_without_provider_key = client.post(
        f"/api/admin/provider-spend/attempts/{reservation.attempt_id}/resolve",
        headers={"X-Admin-API-Key": "admin-secret"},
        json={
            "actual_charge": 0.5,
            "outcome": "charged",
            "source": "provider",
            "idempotency_key": "api-provider-resolution",
            "provider_snapshot_id": authorized.json()["snapshot_id"],
        },
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert resolve_without_provider_key.status_code == 401
