"""Stable, privacy-safe extraction rejection evidence."""

import json
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import select

from argus.extraction.completeness import CompletenessResult
from argus.extraction.models import (
    ExtractedContent,
    ExtractionAttempt,
    ExtractorName,
)
from argus.extraction.rejection import classify_extraction_rejection


def test_successful_complete_extraction_has_no_rejection():
    result = ExtractedContent(
        url="https://example.com/article",
        text="complete content.",
        word_count=2,
        extractor=ExtractorName.TRAFILATURA,
        completeness_result=CompletenessResult(
            is_complete=True,
            confidence=0.05,
            truncation_type="clean",
        ),
        attempts=[
            ExtractionAttempt(
                extractor="trafilatura",
                status="success",
                latency_ms=12,
            )
        ],
    )

    assert classify_extraction_rejection(result) is None


def test_incomplete_content_has_stable_retryable_rejection():
    result = ExtractedContent(
        url="https://example.com/private?token=never-copy-this",
        text="partial content...",
        word_count=2,
        extractor=ExtractorName.TRAFILATURA,
        quality_passed=True,
        completeness_result=CompletenessResult(
            is_complete=False,
            confidence=0.95,
            truncation_type="ellipsis",
            signals=["trailing_ellipsis"],
            recommended_action="try_full_fetch",
        ),
        attempts=[
            ExtractionAttempt(
                extractor="trafilatura",
                status="success",
                latency_ms=17,
            ),
            ExtractionAttempt(
                extractor="jina",
                status="failed",
                latency_ms=23,
                failure_summary="no_content",
            ),
        ],
    )

    rejection = classify_extraction_rejection(result)

    assert rejection is not None
    assert rejection.code == "incomplete_content"
    assert rejection.provider == "trafilatura"
    assert rejection.quality_passed is True
    assert rejection.is_complete is False
    assert rejection.recommended_action == "retry_later"
    assert rejection.attempt_count == 2
    assert rejection.total_latency_ms == 40
    assert "example.com" not in repr(rejection)
    assert "never-copy-this" not in repr(rejection)


def test_quality_gate_failure_is_terminal_and_explicit():
    result = ExtractedContent(
        url="https://example.com/article",
        text="thin",
        word_count=1,
        extractor=ExtractorName.JINA,
        quality_passed=False,
        quality_reason="all_extractors_quality_failed",
        attempts=[
            ExtractionAttempt(
                extractor="jina",
                status="quality_failed",
                latency_ms=9,
                failure_summary="too_short",
            )
        ],
    )

    rejection = classify_extraction_rejection(result)

    assert rejection is not None
    assert rejection.code == "quality_gate_failed"
    assert rejection.provider == "jina"
    assert rejection.recommended_action == "terminal"
    assert rejection.last_status == "quality_failed"


def test_timeout_takes_precedence_over_generic_provider_failures():
    result = ExtractedContent(
        url="https://example.com/article",
        error="all extractors failed",
        quality_passed=False,
        attempts=[
            ExtractionAttempt(
                extractor="trafilatura",
                status="failed",
                latency_ms=10_000,
                failure_summary="ReadTimeout",
            ),
            ExtractionAttempt(
                extractor="jina",
                status="failed",
                latency_ms=4,
                failure_summary="not configured",
            ),
        ],
    )

    rejection = classify_extraction_rejection(result)

    assert rejection is not None
    assert rejection.code == "timeout"
    assert rejection.provider == "trafilatura"
    assert rejection.recommended_action == "retry_later"


def test_unavailable_provider_has_stable_rejection_without_raw_error():
    secret = "Bearer must-not-escape"
    result = ExtractedContent(
        url="https://example.com/article",
        error=f"all extractors failed: {secret}",
        quality_passed=False,
        attempts=[
            ExtractionAttempt(
                extractor="firecrawl",
                status="failed",
                latency_ms=8,
                failure_summary=f"provider unavailable: {secret}",
            )
        ],
    )

    rejection = classify_extraction_rejection(result)

    assert rejection is not None
    assert rejection.code == "provider_unavailable"
    assert rejection.provider == "firecrawl"
    assert rejection.recommended_action == "retry_later"
    assert secret not in repr(rejection)


def test_rejection_drops_untrusted_provider_and_status_labels():
    result = ExtractedContent(
        url="https://example.com/article",
        error="provider unavailable",
        attempts=[
            ExtractionAttempt(
                extractor="jina https://secret.example/?token=hidden",
                status="failed Bearer hidden",
                latency_ms=1,
                failure_summary="provider unavailable",
            )
        ],
    )

    rejection = classify_extraction_rejection(result)

    assert rejection is not None
    assert rejection.provider is None
    assert rejection.last_status is None
    assert "secret.example" not in repr(rejection)
    assert "hidden" not in repr(rejection)


def test_extract_api_exposes_structured_rejection_without_raw_failure(
    tmp_path,
    monkeypatch,
):
    from argus.api.main import create_app
    from argus.persistence.search_ledger import create_search_ledger_repository

    secret = "Bearer never-return-this"
    result = ExtractedContent(
        url="https://example.com/private?token=never-return-this",
        error=f"all extractors failed: {secret}",
        quality_passed=False,
        attempts=[
            ExtractionAttempt(
                extractor="jina",
                status="failed",
                latency_ms=25,
                failure_summary="ReadTimeout",
            )
        ],
    )
    monkeypatch.setattr(
        "argus.api.routes_extract.extract_url",
        AsyncMock(return_value=result),
    )
    broker = MagicMock()
    broker.cache = MagicMock()
    broker.health_tracker = MagicMock()
    broker.budget_tracker = MagicMock()
    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'api-rejection.db'}",
        create_schema=True,
    )
    client = TestClient(create_app(broker=broker, search_repository=repository))

    response = client.post(
        "/api/extract",
        json={"url": "https://example.com/request"},
    )

    assert response.status_code == 200
    rejection = response.json()["rejection"]
    assert rejection == {
        "code": "timeout",
        "provider": "jina",
        "quality_passed": None,
        "is_complete": None,
        "recommended_action": "retry_later",
        "attempt_count": 1,
        "last_status": "failed",
        "total_latency_ms": 25,
    }
    assert secret not in json.dumps(rejection)
    assert "never-return-this" not in json.dumps(rejection)


def test_successful_extract_api_reports_no_rejection(tmp_path, monkeypatch):
    from argus.api.main import create_app
    from argus.persistence.search_ledger import create_search_ledger_repository

    monkeypatch.setattr(
        "argus.api.routes_extract.extract_url",
        AsyncMock(
            return_value=ExtractedContent(
                url="https://example.com/article",
                text="complete.",
                word_count=1,
                extractor=ExtractorName.TRAFILATURA,
                completeness_result=CompletenessResult(
                    is_complete=True,
                    confidence=0.05,
                    truncation_type="clean",
                ),
            )
        ),
    )
    broker = MagicMock()
    broker.cache = MagicMock()
    broker.health_tracker = MagicMock()
    broker.budget_tracker = MagicMock()
    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'api-success.db'}",
        create_schema=True,
    )
    client = TestClient(create_app(broker=broker, search_repository=repository))

    response = client.post(
        "/api/extract",
        json={"url": "https://example.com/article"},
    )

    assert response.status_code == 200
    assert response.json()["rejection"] is None


def test_persisted_rejection_matches_api_shape_and_excludes_raw_evidence(tmp_path):
    from argus.persistence.search_ledger import (
        ExtractionArtifactRow,
        create_search_ledger_repository,
    )

    secret = "Bearer persist-never"
    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'persisted-rejection.db'}",
        create_schema=True,
    )
    result = ExtractedContent(
        url="https://example.com/private?token=persist-never",
        error=f"provider unavailable: {secret}",
        quality_passed=False,
        attempts=[
            ExtractionAttempt(
                extractor="firecrawl",
                status="failed",
                latency_ms=31,
                failure_summary=f"provider unavailable: {secret}",
            )
        ],
    )

    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="atlas",
        result=result,
        latency_ms=34,
        extraction_run_id="structured-rejection",
    )

    with repository.session_factory() as session:
        artifact = session.scalar(select(ExtractionArtifactRow))
    rejection = json.loads(artifact.metadata_json)["rejection"]

    assert rejection["code"] == "provider_unavailable"
    assert rejection["provider"] == "firecrawl"
    assert rejection["attempt_count"] == 1
    assert secret not in json.dumps(rejection)
    assert "example.com" not in json.dumps(rejection)
    assert "persist-never" not in json.dumps(rejection)
