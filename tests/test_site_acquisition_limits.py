"""Regression coverage for bounded site-acquisition search planning."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from argus.broker.accepted import AcceptanceReceipt, AcceptedSearchExecution
from argus.broker.planning import (
    ExecutionPolicySnapshot,
    RetrievalControls,
    resolve_plan,
)
from argus.contracts import CanonicalOutcome
from argus.models import (
    ProviderName,
    ProviderTrace,
    SearchMode,
    SearchResponse,
    SearchResult,
)
from argus.operations.accepted import (
    AcceptedOperationRegistration,
    AcceptedOperationService,
)


def _site_search_response() -> SearchResponse:
    return SearchResponse(
        query="https://example.test",
        mode=SearchMode.DISCOVERY,
        results=(
            SearchResult(
                url="https://example.test/accepted",
                title="Accepted site page",
                snippet="Accepted site page",
                domain="example.test",
                provider=ProviderName.DUCKDUCKGO,
            ),
        ),
        traces=[
            ProviderTrace(
                provider=ProviderName.DUCKDUCKGO,
                status="success",
                results_count=1,
                latency_ms=1,
            )
        ],
        total_results=1,
        search_run_id="site-search",
        created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_site_acquisition_caps_search_without_reducing_discovery_limit(
    monkeypatch,
):
    queries = []

    async def search_accepted(query, **_kwargs):
        queries.append(query)
        resolve_plan(
            query,
            RetrievalControls(),
            False,
            ExecutionPolicySnapshot(),
            datetime(2026, 8, 9, tzinfo=timezone.utc),
        )
        return AcceptedSearchExecution(
            outcome=CanonicalOutcome.SUCCESS,
            reason="accepted",
            response=_site_search_response(),
            receipt=AcceptanceReceipt(
                receipt_ref="site-search",
                accepted_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
                acceptance_fingerprint="a" * 64,
            ),
        )

    broker = MagicMock()
    broker.search_accepted = AsyncMock(side_effect=search_accepted)
    evidence_repository = MagicMock()
    evidence_repository.accept.return_value = AcceptanceReceipt(
        receipt_ref="site-receipt",
        accepted_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        acceptance_fingerprint="b" * 64,
    )
    service = AcceptedOperationService(
        broker_provider=lambda: broker,
        repository_provider=MagicMock(),
        registration=AcceptedOperationRegistration.complete(),
    )
    service._evidence_repository = evidence_repository
    discovered = AsyncMock(
        return_value=(
            "https://example.test/one",
            "https://example.test/two",
            "https://example.test/three",
        )
    )
    monkeypatch.setattr("argus.operations.accepted.discover_site_urls", discovered)

    operation = await service.acquire_site(
        SimpleNamespace(
            url="https://example.test",
            soft_page_limit=2,
            hard_page_limit=120,
            caller="workflow-test",
        ),
        principal="workflow-test",
        request_id="site-acquisition",
    )

    assert operation.outcome is CanonicalOutcome.SUCCESS
    assert queries[0].max_results == 50
    assert discovered.await_args.kwargs["hard_limit"] == 120
    assert len(operation.result["results"]) == 2
    assert all(
        item["url"].startswith("https://example.test")
        for item in operation.result["results"]
    )
