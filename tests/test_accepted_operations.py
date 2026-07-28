from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from argus.api.schemas import ExtractRequest, SearchRequest
from argus.contracts import CanonicalOutcome
from argus.models import (
    ProviderName,
    ProviderTrace,
    SearchMode,
    SearchResponse,
    SearchResult,
)

EVIDENCE_FIXTURES = (
    Path(__file__).parent / "fixtures/contracts/retrieval_evidence_v2"
)


def _search_response(*, results: bool = True) -> SearchResponse:
    return SearchResponse(
        query="accepted operation",
        mode=SearchMode.DISCOVERY,
        results=(
            [
                SearchResult(
                    url="https://example.test/accepted",
                    title="Accepted",
                    snippet="One durable result",
                    domain="example.test",
                    provider=ProviderName.DUCKDUCKGO,
                )
            ]
            if results
            else []
        ),
        traces=[
            ProviderTrace(
                provider=ProviderName.DUCKDUCKGO,
                status="success",
                results_count=1 if results else 0,
            )
        ],
        total_results=1 if results else 0,
        search_run_id="run-accepted",
    )


@pytest.mark.asyncio
async def test_search_executes_and_accepts_once_then_presenter_is_pure():
    from argus.api.presenters import LegacyHttpPresenter
    from argus.operations.accepted import AcceptedOperationService

    broker = MagicMock()
    broker.search = AsyncMock(return_value=_search_response())
    repository = MagicMock()
    repository.accept.return_value.run_id = "run-accepted"
    service = AcceptedOperationService(
        broker_provider=lambda: broker,
        repository_provider=lambda: repository,
    )

    operation = await service.search(
        SearchRequest(query="accepted operation"),
        principal="maya",
        request_id="request-accepted",
    )

    assert operation.outcome is CanonicalOutcome.SUCCESS
    assert isinstance(operation.result, MappingProxyType)
    broker.search.assert_awaited_once()
    repository.accept.assert_called_once()

    presenter = LegacyHttpPresenter()
    first = presenter.search(operation)
    second = presenter.search(operation)
    assert first == second
    assert first.search_run_id == "run-accepted"
    broker.search.assert_awaited_once()
    repository.accept.assert_called_once()


@pytest.mark.asyncio
async def test_evidence_authority_never_calls_legacy_search_or_accept():
    from datetime import datetime, timezone

    from argus.broker.accepted import AcceptanceReceipt, AcceptedSearchExecution
    from argus.operations.accepted import (
        AcceptedOperationRegistration,
        AcceptedOperationService,
    )

    broker = MagicMock()
    broker.search = AsyncMock()
    receipt = AcceptanceReceipt(
        receipt_ref="receipt:evidence",
        accepted_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        acceptance_fingerprint="a" * 64,
    )
    broker.search_accepted = AsyncMock(
        return_value=AcceptedSearchExecution(
            outcome=CanonicalOutcome.SUCCESS,
            reason="accepted",
            response=_search_response(),
            receipt=receipt,
        )
    )
    legacy_repository = MagicMock()
    service = AcceptedOperationService(
        broker_provider=lambda: broker,
        repository_provider=lambda: legacy_repository,
        registration=AcceptedOperationRegistration.complete(),
    )
    evidence_repository = MagicMock()
    service._evidence_repository = evidence_repository

    operation = await service.search(
        SearchRequest(query="accepted operation"),
        principal="maya",
        request_id="request-evidence",
    )

    assert operation.outcome is CanonicalOutcome.SUCCESS
    broker.search_accepted.assert_awaited_once()
    broker.search.assert_not_awaited()
    legacy_repository.accept.assert_not_called()


@pytest.mark.asyncio
async def test_evidence_session_persistence_failure_stays_canonical():
    from argus.broker.accepted import AcceptedSearchExecution
    from argus.operations.accepted import (
        AcceptedOperationRegistration,
        AcceptedOperationService,
    )

    broker = MagicMock()
    broker.session_exists.return_value = True
    broker.search_with_session_accepted = AsyncMock(
        return_value=(
            AcceptedSearchExecution(
                outcome=CanonicalOutcome.PERSISTENCE_FAILED,
                reason="write_failed",
                response=None,
                receipt=None,
            ),
            "owned-session",
        )
    )
    session_authority = MagicMock()
    session_authority.owns.return_value = True
    service = AcceptedOperationService(
        broker_provider=lambda: broker,
        repository_provider=MagicMock(),
        session_authority=session_authority,
        registration=AcceptedOperationRegistration.complete(),
    )
    service._evidence_repository = MagicMock()

    operation = await service.search(
        SearchRequest(
            query="accepted operation",
            session_id="owned-session",
        ),
        principal="maya",
        request_id="request-session-failure",
        require_owned_session=True,
    )

    assert operation.outcome is CanonicalOutcome.PERSISTENCE_FAILED
    assert operation.result is None
    assert operation.error.code == "persistence_failed"


@pytest.mark.asyncio
async def test_evidence_recovery_accepts_archive_fallback_before_persistence(
    tmp_path,
):
    from argus.api.schemas import RecoverUrlRequest
    from argus.broker.router import SearchBroker
    from argus.operations.accepted import (
        AcceptedOperationRegistration,
        AcceptedOperationService,
    )
    from argus.persistence.evidence import SqlAlchemyEvidenceRepository
    from argus.persistence.search_ledger import create_search_ledger_repository

    ledger = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'recovery-evidence.db'}",
        create_schema=True,
    )
    evidence = SqlAlchemyEvidenceRepository(ledger.session_factory)
    broker = SearchBroker(providers={}, spend_repository=MagicMock())
    archive_lookup = AsyncMock(
        return_value={
            "url": "https://archive.example/snapshot",
            "title": "Archived",
            "snippet": "Recovered snapshot",
            "domain": "archive.example",
            "score": 1.0,
        }
    )
    service = AcceptedOperationService(
        broker_provider=lambda: broker,
        repository_provider=lambda: ledger,
        archive_lookup=archive_lookup,
        session_authority=MagicMock(),
        registration=AcceptedOperationRegistration.complete(),
    )
    service._evidence_repository = evidence

    operation = await service.recover(
        RecoverUrlRequest(url="https://dead.example/article"),
        principal="maya",
        request_id="request-recovery",
    )

    assert operation.outcome is CanonicalOutcome.SUCCESS
    assert operation.result["results"][0]["provider"] == "archive_ph"
    assert [
        trace["status"]
        for trace in operation.result["traces"]
        if trace["provider"] == "archive_ph"
    ] == ["success"]
    assert evidence.accepted_count() == 1
    archive_lookup.assert_awaited_once()


def test_legacy_search_presenter_preserves_failure_trace_response():
    from argus.api.presenters import LegacyHttpPresenter
    from argus.contracts import AcceptedOperation, OperationError

    operation = AcceptedOperation(
        outcome=CanonicalOutcome.PROVIDERS_FAILED,
        request_id="request-v1-failure",
        result={
            "query": "failed",
            "mode": "discovery",
            "results": [],
            "traces": [
                {
                    "provider": "duckduckgo",
                    "status": "error",
                    "results_count": 0,
                    "latency_ms": 0,
                    "error": "provider unavailable",
                    "budget_remaining": None,
                }
            ],
            "total_results": 0,
            "cached": False,
            "budget_warnings": [],
            "search_run_id": "run-failed",
            "session_id": None,
        },
        error=OperationError(
            outcome=CanonicalOutcome.PROVIDERS_FAILED,
            type="urn:argus:problem:providers_failed",
            title="Providers Failed",
            status=502,
            detail="Providers failed",
            instance="urn:argus:request:request-v1-failure",
            code="providers_failed",
            retryable=False,
            retry_after_seconds=None,
        ),
    )

    response = LegacyHttpPresenter().search(operation)

    assert response.search_run_id == "run-failed"
    assert response.traces[0].status == "error"


@pytest.mark.asyncio
async def test_empty_search_is_an_accepted_empty_outcome():
    from argus.operations.accepted import AcceptedOperationService

    broker = MagicMock()
    broker.search = AsyncMock(return_value=_search_response(results=False))
    repository = MagicMock()
    service = AcceptedOperationService(
        broker_provider=lambda: broker,
        repository_provider=lambda: repository,
    )

    operation = await service.search(
        SearchRequest(query="accepted operation"),
        principal="maya",
        request_id="request-empty",
    )

    assert operation.outcome is CanonicalOutcome.EMPTY
    repository.accept.assert_called_once()


@pytest.mark.asyncio
async def test_persistence_failure_is_visible_and_never_reexecutes():
    from argus.api.presenters import LegacyHttpPresenter
    from argus.operations.accepted import AcceptedOperationService

    broker = MagicMock()
    broker.search = AsyncMock(return_value=_search_response())
    repository = MagicMock()
    repository.accept.side_effect = RuntimeError("database password must not leak")
    service = AcceptedOperationService(
        broker_provider=lambda: broker,
        repository_provider=lambda: repository,
    )

    operation = await service.search(
        SearchRequest(query="accepted operation"),
        principal="maya",
        request_id="request-persistence",
    )

    assert operation.outcome is CanonicalOutcome.PERSISTENCE_FAILED
    assert operation.error.detail == "Search could not be durably accepted"
    with pytest.raises(HTTPException) as raised:
        LegacyHttpPresenter().search(operation)
    assert raised.value.status_code == 503
    assert raised.value.detail == "Search could not be durably accepted"
    broker.search.assert_awaited_once()
    repository.accept.assert_called_once()


@pytest.mark.asyncio
async def test_v2_retrieval_session_is_opaque_principal_bound_and_durable():
    from argus.api.security import RetrievalSessionAuthority
    from argus.operations.accepted import AcceptedOperationService

    authority = RetrievalSessionAuthority(b"s" * 32)
    broker = MagicMock()
    broker.search_with_session = AsyncMock(
        side_effect=lambda query, *, session_id, **kwargs: (
            _search_response(),
            session_id,
        )
    )
    repository = MagicMock()
    service = AcceptedOperationService(
        broker_provider=lambda: broker,
        repository_provider=lambda: repository,
        session_authority=authority,
    )

    operation = await service.search(
        SearchRequest(query="new owned session"),
        principal="maya",
        request_id="request-owned-session",
        require_owned_session=True,
    )

    session_id = operation.result["session_id"]
    assert authority.owns(session_id, "maya")
    assert not authority.owns(session_id, "hermes")
    broker.search_with_session.assert_awaited_once()
    repository.accept.assert_called_once()


@pytest.mark.asyncio
async def test_wrong_principal_session_rejects_before_broker_or_persistence():
    from argus.api.security import RetrievalSessionAuthority
    from argus.operations.accepted import AcceptedOperationService

    authority = RetrievalSessionAuthority(b"s" * 32)
    broker_provider = MagicMock()
    repository_provider = MagicMock()
    service = AcceptedOperationService(
        broker_provider=broker_provider,
        repository_provider=repository_provider,
        session_authority=authority,
    )

    operation = await service.search(
        SearchRequest(
            query="stolen session",
            session_id=authority.issue("maya"),
        ),
        principal="hermes",
        request_id="request-wrong-principal",
        require_owned_session=True,
    )

    assert operation.outcome is CanonicalOutcome.UNREADY
    assert operation.error.status == 404
    assert operation.error.code == "session_not_found"
    broker_provider.assert_not_called()
    repository_provider.assert_not_called()


@pytest.mark.asyncio
async def test_extraction_executes_once_and_projects_without_reclassification():
    from argus.api.presenters import LegacyHttpPresenter
    from argus.extraction.models import ExtractedContent, ExtractorName
    from argus.operations.accepted import AcceptedOperationService

    accepted = ExtractedContent(
        extraction_run_id="extract-accepted",
        url="https://example.test/article",
        title="Article",
        text="accepted text",
        word_count=2,
        extractor=ExtractorName.TRAFILATURA,
        quality_passed=True,
    )
    extract = AsyncMock(return_value=accepted)
    service = AcceptedOperationService(
        broker_provider=MagicMock(),
        repository_provider=lambda: MagicMock(),
        extractor=extract,
    )

    operation = await service.extract(
        ExtractRequest(url="https://example.test/article"),
        principal="maya",
        request_id="request-extract",
    )

    response = LegacyHttpPresenter().extract(operation)
    assert response.extraction_run_id == "extract-accepted"
    assert response.text == "accepted text"
    extract.assert_awaited_once()


def test_evidence_authority_requires_one_complete_registration():
    from argus.operations.accepted import (
        AcceptedAuthorityConfigurationError,
        AcceptedOperationRegistration,
    )

    with pytest.raises(AcceptedAuthorityConfigurationError, match="missing"):
        AcceptedOperationRegistration(
            planner=True,
            readiness=True,
            evidence_repository=True,
            extraction_finalizer=True,
            legacy_presenters=False,
        ).validate("evidence")

    AcceptedOperationRegistration.complete().validate("evidence")
    AcceptedOperationRegistration().validate("legacy")


def test_http_search_route_calls_only_the_accepted_operation_service(monkeypatch):
    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.contracts import AcceptedOperation

    service = MagicMock()
    service.search = AsyncMock(
        return_value=AcceptedOperation(
            outcome=CanonicalOutcome.SUCCESS,
            request_id="request-route",
            result={
                "query": "route",
                "mode": "discovery",
                "results": [],
                "traces": [],
                "total_results": 0,
                "cached": False,
                "budget_warnings": [],
                "search_run_id": "run-route",
                "session_id": None,
                "acceptance_receipt": {
                    "run_id": "run-route",
                    "delivery_intent_id": None,
                },
            },
            error=None,
        )
    )
    client = TestClient(create_app(accepted_operation_service=service))

    response = client.post("/api/search", json={"query": "route"})

    assert response.status_code == 200
    assert response.json()["search_run_id"] == "run-route"
    service.search.assert_awaited_once()


def test_app_rejects_partial_evidence_authority_registration(monkeypatch):
    from argus.api.main import create_app
    from argus.config import reset_config
    from argus.operations.accepted import (
        AcceptedAuthorityConfigurationError,
        AcceptedOperationRegistration,
        AcceptedOperationService,
    )

    monkeypatch.setenv("ARGUS_ACCEPTED_OPERATION_AUTHORITY", "evidence")
    reset_config()
    try:
        partial = AcceptedOperationService(
            broker_provider=MagicMock(),
            repository_provider=MagicMock(),
            registration=AcceptedOperationRegistration(planner=True),
        )
        with pytest.raises(AcceptedAuthorityConfigurationError, match="missing"):
            create_app(accepted_operation_service=partial)
    finally:
        reset_config()


def test_production_evidence_validator_replays_all_frozen_scenarios_and_mutations():
    from argus.contracts.evidence import (
        AcceptedEvidenceEnvelope,
        RetrievalEvidenceContractViolation,
    )

    manifest = json.loads((EVIDENCE_FIXTURES / "manifest.json").read_text())
    for entry in manifest["fixtures"]:
        envelope = json.loads((EVIDENCE_FIXTURES / entry["path"]).read_text())
        if entry["kind"] == "valid":
            accepted = AcceptedEvidenceEnvelope.from_mapping(
                envelope["result"]["evidence"]
            )
            assert accepted.evidence["request"]["request_id"]
        else:
            with pytest.raises(RetrievalEvidenceContractViolation) as rejected:
                AcceptedEvidenceEnvelope.from_mapping(
                    envelope["result"]["evidence"]
                )
            assert any(
                entry["expected_invariant"] in violation
                for violation in rejected.value.violations
            ), (entry["path"], rejected.value.violations)
