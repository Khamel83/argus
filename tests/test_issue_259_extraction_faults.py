"""Regression coverage for issue #259 (Third Street caller failures)."""

from datetime import datetime, timezone

import pytest

from argus.extraction.models import (
    ExtractedContent,
    ExtractionAttempt,
    ExtractorName,
)


def _repository(tmp_path, name="issue-259.db"):
    from argus.persistence.search_ledger import create_search_ledger_repository

    return create_search_ledger_repository(
        f"sqlite:///{tmp_path / name}",
        create_schema=True,
    )


def _finalize(result, *, url, repository, request_id):
    from argus.extraction.extractor import _finalize_accepted_extraction

    return _finalize_accepted_extraction(
        result,
        url=url,
        mode="default",
        caller="issue-259",
        request_id=request_id,
        latency_ms=1,
        repository=repository,
    )


def test_durable_cache_lookup_failure_is_a_miss_not_a_failed_extraction():
    """A statement deadline in the cache read must not fail the request."""
    from argus.extraction.cache import ExtractionCache
    from argus.extraction.extractor import _accepted_cache_identity
    from argus.extraction.outcomes import CacheOutcome

    class _TimedOutRepository:
        calls = 0

        def find_extraction_outcomes_by_url_identity(self, identity, *, mode=None):
            self.calls += 1
            raise RuntimeError("canceling statement due to statement timeout")

    repository = _TimedOutRepository()
    decision = ExtractionCache(acceptance_repository=repository).decide(
        _accepted_cache_identity("https://example.com/a", "default", True),
        acceptance_repository=repository,
        now=datetime(2026, 11, 27, tzinfo=timezone.utc),
    )

    assert repository.calls == 1
    assert decision.outcome is CacheOutcome.MISS


def test_durable_cache_lookup_returns_each_receipt_once(tmp_path):
    """The bounded legacy window does not duplicate identity matches."""
    repository = _repository(tmp_path)
    url = "https://example.com/issue-259"
    receipts = []
    for index in range(2):
        accepted = _finalize(
            ExtractedContent(
                url=url,
                title="Issue 259",
                text="durable cache lookup content " * 50,
                word_count=200,
                extractor=ExtractorName.TRAFILATURA,
            ),
            url=url,
            repository=repository,
            request_id=f"issue-259-dedupe-{index}",
        )
        receipts.append(accepted.acceptance_receipt.receipt_ref)

    from argus.extraction.extractor import _accepted_cache_identity

    identity = _accepted_cache_identity(url, "default", True).normalized_url
    matches = repository.find_extraction_outcomes_by_url_identity(
        identity, mode="default"
    )
    found = [item.acceptance_receipt.receipt_ref for item in matches]

    assert sorted(found) == sorted(receipts)
    assert len(found) == len(set(found))


@pytest.mark.parametrize(
    ("extractor", "archived"),
    [
        (ExtractorName.WAYBACK, True),
        (ExtractorName.ARCHIVE_IS, True),
        (ExtractorName.TRAFILATURA, False),
    ],
)
def test_accepted_projection_reports_archive_provenance(tmp_path, extractor, archived):
    """Wayback content must not be reported to callers as a live page."""
    from argus.extraction.extractor import _populate_provenance

    url = f"https://example.com/{extractor.value}"
    result = ExtractedContent(
        url=url,
        title="Archived page",
        text="archived page content " * 60,
        word_count=180,
        extractor=extractor,
    )
    _populate_provenance(result)
    projected = _finalize(
        result,
        url=url,
        repository=_repository(tmp_path),
        request_id=f"issue-259-archive-{extractor.value}",
    )

    assert projected.archive_used is archived
    assert (projected.source_type == "archive") is archived


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ("trafilatura: HTTP 403", "provider_policy_rejected"),
        ("trafilatura: acquisition_blocked", "provider_policy_rejected"),
        ("trafilatura: HTTP 429", "rate_limited"),
        ("trafilatura: HTTP 503", "provider_unavailable"),
        ("obscura: browser policy unavailable", "provider_unavailable"),
        (
            "browser_policy_unavailable:browser connection policy is unavailable",
            "provider_unavailable",
        ),
        ("TimeoutError", "timeout"),
        ("wayback: not archived", "empty"),
        ("archive_is: no existing archive found", "empty"),
        ("GuardedHTTPStatusError", "unknown_failure"),
        (None, "unknown_failure"),
    ],
)
def test_failed_attempt_summary_maps_to_a_clear_reason(summary, expected):
    from argus.extraction.extractor import _failed_attempt_outcome

    assert _failed_attempt_outcome(summary).value == expected


def test_blocked_chain_evidence_names_each_attempt_reason(tmp_path):
    """An origin block stays an error, with a reason per attempt."""
    url = "https://blocked.example/feed.rss"
    attempts = [
        ExtractionAttempt("trafilatura", "failed", 300, "trafilatura: HTTP 403"),
        ExtractionAttempt(
            "obscura", "failed", 0, "obscura: browser policy unavailable"
        ),
        ExtractionAttempt("wayback", "failed", 400, "wayback: not archived"),
    ]
    projected = _finalize(
        ExtractedContent(
            url=url,
            error="all extractors failed",
            attempts=attempts,
        ),
        url=url,
        repository=_repository(tmp_path),
        request_id="issue-259-blocked",
    )

    assert projected.error
    assert [attempt.status for attempt in projected.attempts] == [
        "provider_policy_rejected",
        "provider_unavailable",
        "empty",
    ]


@pytest.mark.asyncio
async def test_trafilatura_origin_refusal_is_a_bounded_attempt_result(monkeypatch):
    from argus.acquisition.guarded import GuardedHTTPStatusError
    from argus.extraction import extractor as extractor_module

    class _Response:
        status_code = 403

    async def refuse(*args, **kwargs):
        raise GuardedHTTPStatusError(_Response())

    monkeypatch.setattr(extractor_module, "guarded_http_request", refuse)
    result = await extractor_module._extract_trafilatura("https://blocked.example/")

    assert result.error == "trafilatura: HTTP 403"
    assert not result.text
