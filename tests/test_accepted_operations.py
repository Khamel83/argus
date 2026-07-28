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


def test_session_update_failure_is_separate_from_receipt_bound_outcome():
    from datetime import datetime, timezone

    from argus.broker.accepted import (
        AcceptanceReceipt,
        AcceptedSearchExecution,
    )
    from argus.operations.accepted import AcceptedOperationService

    service = AcceptedOperationService(
        broker_provider=MagicMock(),
        repository_provider=MagicMock(),
    )
    operation = service._accepted_search_operation(
        AcceptedSearchExecution(
            outcome=CanonicalOutcome.SUCCESS,
            reason="accepted",
            response=_search_response(),
            receipt=AcceptanceReceipt(
                receipt_ref="receipt:session-warning",
                accepted_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
                acceptance_fingerprint="a" * 64,
            ),
            session_update_failed=True,
        ),
        request_id="request-session-warning",
        include_attribution=False,
        session_id="owned-session",
    )

    assert operation.outcome is CanonicalOutcome.SUCCESS
    assert operation.result["session_update"] == {
        "status": "failed",
        "reason": "session_update_failed",
    }
    assert operation.result["acceptance_receipt"]["acceptance_fingerprint"] == (
        "a" * 64
    )


@pytest.mark.asyncio
async def test_extraction_presenter_preserves_accepted_preflight_outcome():
    from argus.extraction.models import ExtractedContent
    from argus.operations.accepted import AcceptedOperationService

    async def rejected_extractor(*_args, **_kwargs):
        return ExtractedContent(
            url="https://blocked.example",
            error="policy_rejected",
            accepted_outcome=CanonicalOutcome.POLICY_REJECTED,
        )

    service = AcceptedOperationService(
        broker_provider=MagicMock(),
        repository_provider=MagicMock(),
        extractor=rejected_extractor,
    )
    operation = await service.extract(
        ExtractRequest(url="https://blocked.example"),
        principal="maya",
        request_id="request-extraction-preflight",
    )

    assert operation.outcome is CanonicalOutcome.POLICY_REJECTED
    assert operation.error.status == 403


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


@pytest.mark.asyncio
async def test_archive_fallback_is_cancelled_at_operation_deadline(tmp_path):
    import asyncio
    from datetime import datetime, timezone

    from argus.broker.planning import (
        ExecutionPolicySnapshot,
        RetrievalControls,
        resolve_plan,
    )
    from argus.broker.router import SearchBroker
    from argus.models import SearchQuery
    from argus.persistence.evidence import SqlAlchemyEvidenceRepository
    from argus.persistence.search_ledger import create_search_ledger_repository

    ledger = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'archive-deadline.db'}",
        create_schema=True,
    )
    evidence = SqlAlchemyEvidenceRepository(ledger.session_factory)
    broker = SearchBroker(providers={}, spend_repository=MagicMock())
    query = SearchQuery(
        query="https://dead.example/article",
        mode=SearchMode.RECOVERY,
    )
    broker._accepted_plan = lambda _query, *, compute_attribution: resolve_plan(
        _query,
        RetrievalControls(deadline_ms=1_050),
        compute_attribution,
        ExecutionPolicySnapshot(),
        lambda: datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    cancelled = False

    async def blocked_archive_lookup():
        nonlocal cancelled
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled = True
            raise

    captured = None

    class CapturingRepository:
        def accept(self, item):
            nonlocal captured
            captured = item
            return evidence.accept(item)

    execution = await broker.search_accepted(
        query,
        evidence_repository=CapturingRepository(),
        empty_fallback=blocked_archive_lookup,
    )

    assert execution.outcome is CanonicalOutcome.TIMEOUT
    assert execution.reason == "operation_deadline"
    assert cancelled is True
    assert captured.fusion is None
    assert evidence.accepted_count() == 1


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


def _replay_valid_fixture_through_production(evidence, database_path):
    from dataclasses import replace
    from datetime import datetime, timezone

    from argus.api.contracts_v2 import EvidenceHttpPresenter
    from argus.broker.accepted import (
        AcceptanceReceipt,
        AcceptedRetrieval,
        acceptance_fingerprint,
        canonical_cache_outcome,
        execution_cohort,
    )
    from argus.broker.budgets import PROVIDER_TIERS
    from argus.broker.fusion import (
        PSL_DOMAIN_POLICY_VERSION,
        fuse_evidence,
        project_search_results,
    )
    from argus.broker.planning import (
        ExecutionPolicySnapshot,
        RetrievalControls,
        resolve_plan,
    )
    from argus.broker.provider_evidence import LegacyProviderBatchAdapter
    from argus.contracts import AcceptedOperation
    from argus.extraction.completeness import CompletenessResult
    from argus.extraction.extractor import _finalize_accepted_extraction
    from argus.extraction.models import (
        ExtractedContent,
        ExtractionAttempt,
        ExtractorName,
    )
    from argus.models import (
        FusionPolicy,
        ProviderName,
        ProviderTrace,
        SearchMode,
        SearchQuery,
        SearchResult,
    )
    from argus.operations.accepted import _operation_error
    from argus.persistence.evidence import (
        RetrievalEvidence,
        SqlAlchemyEvidenceRepository,
    )
    from argus.persistence.search_ledger import create_search_ledger_repository

    all_attempts = [
        *evidence["origin_provider_attempts"],
        *evidence["provider_attempts"],
    ]
    provider_names = tuple(
        ProviderName(value)
        for value in dict.fromkeys(
            [
                *evidence["plan"]["candidate_providers"],
                *(item["provider"] for item in all_attempts),
            ]
        )
    )
    query = SearchQuery(
        query=f"fixture {evidence['request']['request_id']}",
        mode=SearchMode(evidence["request"]["mode"]),
        max_results=max(1, evidence["plan"]["result_limit"]),
        providers=list(provider_names) or None,
        free_only=False,
        caller=evidence["request"]["caller_ref"],
    )
    plan = resolve_plan(
        query,
        RetrievalControls(),
        evidence["plan"]["controls"]["include_attribution"],
        ExecutionPolicySnapshot(
            effective_max_provider_tier=max(
                (PROVIDER_TIERS[provider] for provider in provider_names),
                default=0,
            ),
            allowed_providers=provider_names or None,
            domain_policy_version=PSL_DOMAIN_POLICY_VERSION,
        ),
        lambda: datetime(2026, 7, 27, 5, tzinfo=timezone.utc),
    )
    attempts = {
        item["provider"]: item
        for item in all_attempts
    }
    batches = []
    for provider_name, attempt in attempts.items():
        provider = ProviderName(provider_name)
        results = []
        for cluster in evidence["fusion"]["clusters"]:
            contribution = next(
                (
                    item
                    for item in cluster["contributions"]
                    if item["provider"] == provider_name
                ),
                None,
            )
            if contribution is not None:
                results.append(
                    SearchResult(
                        url=cluster["url"],
                        title=cluster["title"],
                        snippet="fixture evidence",
                        provider=provider,
                        raw_rank=contribution["provider_rank"],
                    )
                )
        trace = ProviderTrace(
            provider=provider,
            status="success" if results else "error",
            results_count=len(results),
            error=None if results else attempt["outcome"],
        )
        batch = LegacyProviderBatchAdapter.from_legacy((results, trace))
        batches.append(
            replace(
                batch,
                request_evidence=replace(
                    batch.request_evidence,
                    attempt_id=attempt["attempt_id"],
                ),
            )
        )
    fusion = (
        fuse_evidence(
            plan,
            tuple(batches),
            FusionPolicy(),
            lambda: datetime(2026, 7, 27, 5, tzinfo=timezone.utc),
        )
        if batches
        else None
    )
    outcome = fusion.outcome if fusion is not None else CanonicalOutcome.UNREADY
    reason = fusion.reason if fusion is not None else "no_reachable_provider"
    cache_outcome = canonical_cache_outcome(outcome, reason=reason)
    results = (
        project_search_results(fusion)
        if fusion is not None
        else []
    )
    projected_results = tuple(
        {
            "url": item.url,
            "title": item.title,
            "snippet": item.snippet,
            "domain": item.domain,
            "provider": item.provider.value if item.provider else None,
            "score": item.score,
        }
        for item in results
    )
    contributor_refs = tuple(
        batch.request_evidence.attempt_id
        for batch in batches
        if batch.request_evidence.attempt_id
    )
    operation_id = f"replay-{evidence['request']['run_id']}"
    cohort = execution_cohort(
        plan,
        policy_identity=evidence["request"]["caller_ref"],
    )
    fingerprint = acceptance_fingerprint(
        operation_id=operation_id,
        plan_id=plan.plan_id,
        cache_fingerprint=plan.cache_fingerprint,
        execution_cohort_id=cohort,
        outcome=cache_outcome,
        reason=reason,
        results=projected_results,
        contributor_attempt_refs=contributor_refs,
        origin_spend_usd="0",
    )
    receipt = AcceptanceReceipt(
        receipt_ref=f"receipt:{operation_id}",
        accepted_at=datetime(2026, 7, 27, 5, tzinfo=timezone.utc),
        acceptance_fingerprint=fingerprint,
    )
    accepted = AcceptedRetrieval(
        operation_id=operation_id,
        plan_id=plan.plan_id,
        cache_fingerprint=plan.cache_fingerprint,
        execution_cohort=cohort,
        outcome=cache_outcome,
        reason=reason,
        query=query.query,
        mode=query.mode.value,
        results=projected_results,
        contributor_attempt_refs=contributor_refs,
        origin_spend_usd="0",
        acceptance_receipt=receipt,
    )
    ledger = create_search_ledger_repository(
        f"sqlite:///{database_path}",
        create_schema=True,
    )
    repository = SqlAlchemyEvidenceRepository(ledger.session_factory)
    repository.accept(
        RetrievalEvidence(
            accepted=accepted,
            plan=plan,
            provider_batches=tuple(batches),
            fusion=fusion,
        )
    )
    assert repository.accepted_count() == 1

    for extraction in evidence["extractions"]:
        artifact = extraction["artifact"]
        selected = extraction["selected_extractor"]
        extracted = ExtractedContent(
            url=f"https://fixture.invalid/{extraction['cluster_ref']}",
            text="fixture artifact" if artifact else "",
            title="Fixture",
            word_count=2 if artifact else 0,
            extractor=ExtractorName(selected) if selected else None,
            error=None if artifact else "fixture extraction failed",
            quality_passed=(
                artifact["quality_passed"] if artifact else False
            ),
            attempts=[
                ExtractionAttempt(
                    extractor=selected or "trafilatura",
                    status="success" if artifact else "failed",
                    latency_ms=1,
                )
            ],
            completeness_result=(
                CompletenessResult(
                    is_complete=artifact["is_complete"],
                    confidence=1.0,
                    truncation_type="clean",
                    signals=["fixture"],
                    word_count=2,
                )
                if artifact
                else None
            ),
        )
        projected = _finalize_accepted_extraction(
            extracted,
            url=extracted.url,
            mode="default",
            caller=query.caller,
            request_id=f"extract-{operation_id}",
            latency_ms=1,
            repository=ledger,
        )
        assert projected.accepted_outcome is CanonicalOutcome(
            extraction["outcome"]
        )

    response = EvidenceHttpPresenter().response(
        AcceptedOperation(
            outcome=outcome,
            request_id=evidence["request"]["request_id"],
            result={"accepted": True},
            error=(
                None
                if outcome
                in {
                    CanonicalOutcome.SUCCESS,
                    CanonicalOutcome.DEGRADED,
                    CanonicalOutcome.EMPTY,
                }
                else _operation_error(
                    outcome,
                    request_id=evidence["request"]["request_id"],
                    detail="Frozen production replay",
                )
            ),
        )
    )
    assert response.status_code in {200, 502, 503, 504}


async def _replay_fixture_through_accepted_authority(
    envelope,
    database_path,
):
    from dataclasses import replace
    from datetime import datetime, timezone

    from argus.api.contracts_v2 import EvidenceHttpPresenter
    from argus.broker.accepted import (
        AcceptanceReceipt,
        AcceptedRetrieval,
        CacheEntry,
        CacheOutcome,
        acceptance_fingerprint,
        execution_cohort,
    )
    from argus.broker.execution import ProviderExecutionOutcome
    from argus.broker.provider_evidence import LegacyProviderBatchAdapter
    from argus.broker.router import SearchBroker
    from argus.models import SearchQuery
    from argus.operations.accepted import (
        AcceptedOperationRegistration,
        AcceptedOperationService,
    )
    from argus.persistence.evidence import SqlAlchemyEvidenceRepository
    from argus.persistence.search_ledger import create_search_ledger_repository

    evidence = envelope["result"]["evidence"]
    provider_names = tuple(
        ProviderName(value) for value in evidence["plan"]["candidate_providers"]
    )
    batches = {}
    traces = []
    provider_results = {}
    for attempt in evidence["provider_attempts"]:
        provider = ProviderName(attempt["provider"])
        results = []
        for cluster in evidence["fusion"]["clusters"]:
            contribution = next(
                (
                    item
                    for item in cluster["contributions"]
                    if item["attempt_ref"] == attempt["attempt_id"]
                ),
                None,
            )
            if contribution is not None:
                results.append(
                    SearchResult(
                        url=cluster["url"],
                        title=cluster["title"],
                        snippet="fixture evidence",
                        provider=provider,
                        raw_rank=contribution["provider_rank"],
                    )
                )
        trace = ProviderTrace(
            provider=provider,
            status="success" if attempt["outcome"] == "success" else "error",
            results_count=len(results),
            error=(
                None
                if attempt["outcome"] == "success"
                else attempt["outcome"]
            ),
        )
        batch = LegacyProviderBatchAdapter.from_legacy((results, trace))
        batches[provider.value] = replace(
            batch,
            request_evidence=replace(
                batch.request_evidence,
                attempt_id=attempt["attempt_id"],
            ),
        )
        traces.append(trace)
        provider_results[provider.value] = results

    class FixtureExecutor:
        async def execute(self, *_args, **_kwargs):
            return ProviderExecutionOutcome(
                traces=list(traces),
                provider_results=dict(provider_results),
                live_providers_used=len(batches),
                provider_batches=dict(batches),
            )

    ledger = create_search_ledger_repository(
        f"sqlite:///{database_path}",
        create_schema=True,
    )
    repository = SqlAlchemyEvidenceRepository(ledger.session_factory)
    broker = SearchBroker(
        providers={},
        spend_repository=MagicMock(),
        executor=FixtureExecutor(),
    )
    request = SearchRequest(
        query=f"fixture {evidence['request']['request_id']}",
        mode=evidence["request"]["mode"],
        max_results=max(1, evidence["plan"]["result_limit"]),
        providers=[provider.value for provider in provider_names],
    )
    query = SearchQuery(
        query=request.query,
        mode=SearchMode(request.mode),
        max_results=request.max_results,
        providers=list(provider_names),
        caller=evidence["request"]["caller_ref"],
        metadata={"caller_label": request.caller},
    )

    if evidence["cache"]["decision"] in {"hit", "rejected"}:
        plan = broker._accepted_plan(query, compute_attribution=True)
        cohort = execution_cohort(
            plan,
            policy_identity=evidence["request"]["caller_ref"],
        )
        origin_results = tuple(
            {
                "url": cluster["url"],
                "title": cluster["title"],
                "snippet": "fixture cached evidence",
                "domain": cluster["site_key"],
                "provider": cluster["contributions"][0]["provider"],
                "score": 1.0,
            }
            for cluster in evidence["fusion"]["clusters"]
        )
        origin_refs = tuple(
            attempt["attempt_id"]
            for attempt in evidence["origin_provider_attempts"]
        ) or ("fixture-cache-origin",)
        operation_id = f"origin-{evidence['request']['run_id']}"
        fingerprint = acceptance_fingerprint(
            operation_id=operation_id,
            plan_id=plan.plan_id,
            cache_fingerprint=plan.cache_fingerprint,
            execution_cohort_id=cohort,
            outcome=CacheOutcome.SUCCESS,
            reason="accepted",
            results=origin_results,
            contributor_attempt_refs=origin_refs,
            origin_spend_usd="0",
        )
        accepted_at = (
            datetime(2000, 1, 1, tzinfo=timezone.utc)
            if evidence["cache"]["decision"] == "rejected"
            else datetime.now(timezone.utc)
        )
        origin = AcceptedRetrieval(
            operation_id=operation_id,
            plan_id=plan.plan_id,
            cache_fingerprint=plan.cache_fingerprint,
            execution_cohort=cohort,
            outcome=CacheOutcome.SUCCESS,
            reason="accepted",
            query=query.query,
            mode=query.mode.value,
            results=origin_results,
            contributor_attempt_refs=origin_refs,
            origin_spend_usd="0",
            acceptance_receipt=AcceptanceReceipt(
                receipt_ref=f"receipt:{operation_id}",
                accepted_at=accepted_at,
                acceptance_fingerprint=fingerprint,
            ),
        )
        broker._accepted_retrieval_cache.publish(
            CacheEntry.from_accepted(origin, accepted_at=accepted_at)
        )

    evidence_repository = repository
    if envelope["outcome"] == CanonicalOutcome.PERSISTENCE_FAILED.value:
        class FailingEvidenceRepository:
            def accept(self, _evidence):
                raise RuntimeError("fixture persistence failure")

        evidence_repository = FailingEvidenceRepository()

    service = AcceptedOperationService(
        broker_provider=lambda: broker,
        repository_provider=lambda: ledger,
        session_authority=MagicMock(),
        registration=AcceptedOperationRegistration.complete(),
    )
    service._evidence_repository = evidence_repository
    operation = await service.search(
        request,
        principal=evidence["request"]["caller_ref"],
        request_id=evidence["request"]["request_id"],
    )
    expected = (
        CanonicalOutcome.SUCCESS
        if envelope["outcome"] == CanonicalOutcome.EXTRACTION_FAILED.value
        else CanonicalOutcome(envelope["outcome"])
    )
    assert operation.outcome is expected
    response = EvidenceHttpPresenter().response(operation)
    rendered = json.loads(response.body)
    assert rendered["outcome"] == expected.value
    assert rendered["request_id"] == evidence["request"]["request_id"]
    if expected in {CanonicalOutcome.SUCCESS, CanonicalOutcome.DEGRADED}:
        assert {
            item["url"] for item in rendered["result"]["results"]
        } == {
            cluster["url"] for cluster in evidence["fusion"]["clusters"]
        }


@pytest.mark.asyncio
async def test_frozen_valid_fixtures_cross_the_complete_accepted_authority(
    tmp_path,
):
    manifest = json.loads((EVIDENCE_FIXTURES / "manifest.json").read_text())
    for entry in manifest["fixtures"]:
        if entry["kind"] != "valid":
            continue
        envelope = json.loads((EVIDENCE_FIXTURES / entry["path"]).read_text())
        await _replay_fixture_through_accepted_authority(
            envelope,
            tmp_path / f"authority-{entry['path']}.db",
        )


def test_production_evidence_validator_replays_all_frozen_scenarios_and_mutations(
    tmp_path,
):
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
            _replay_valid_fixture_through_production(
                envelope["result"]["evidence"],
                tmp_path / f"{entry['path']}.db",
            )
        else:
            with pytest.raises(RetrievalEvidenceContractViolation) as rejected:
                AcceptedEvidenceEnvelope.from_mapping(
                    envelope["result"]["evidence"]
                )
            assert any(
                entry["expected_invariant"] in violation
                for violation in rejected.value.violations
            ), (entry["path"], rejected.value.violations)
