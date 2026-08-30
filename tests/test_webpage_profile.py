"""Tests for the explicit webpage extraction profile."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from argus.api.schemas import ExtractRequest
from argus.extraction.extractor import (
    _accepted_cache_identity,
    _cache,
    _legacy_cache_key,
    _run_quality_gate,
)
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.extraction.quality_gate import GateResult, QualityGate


def test_extract_request_defaults_to_article_and_restricts_profiles():
    assert ExtractRequest(url="https://example.com/page").content_type == "article"
    assert ExtractRequest(
        url="https://example.com/page", content_type="webpage"
    ).content_type == "webpage"

    with pytest.raises(ValueError):
        ExtractRequest(url="https://example.com/page", content_type="video")


def test_webpage_quality_profile_accepts_a_visible_75_word_page():
    text = " ".join(["visible"] * 75)

    result = QualityGate().evaluate(
        text,
        "https://www.jpl.nasa.gov/explore-jpl/",
        content_type="webpage",
        extractor="playwright",
    )

    assert result.decision is GateResult.PASS
    assert _run_quality_gate(
        text,
        "https://www.jpl.nasa.gov/explore-jpl/",
        "playwright",
        content_type="webpage",
    ) == (True, "")


def test_webpage_quality_profile_still_rejects_below_50_words():
    result = QualityGate().evaluate(
        " ".join(["visible"] * 49),
        "https://example.com/page",
        content_type="webpage",
        extractor="playwright",
    )

    assert result.decision is GateResult.FAIL
    assert "too_few_words" in result.reason


def test_accepted_cache_identity_separates_quality_profiles():
    article = _accepted_cache_identity("https://example.com/page", "default", False)
    webpage = _accepted_cache_identity(
        "https://example.com/page", "default", False, content_type="webpage"
    )

    assert article.quality_policy_version == "quality-v1"
    assert webpage.quality_policy_version == "quality-v1-webpage"
    assert article != webpage


def test_legacy_cache_keys_separate_quality_profiles():
    url = "https://example.com/page"
    article_key = _legacy_cache_key(url, "article")
    webpage_key = _legacy_cache_key(url, "webpage")
    article = ExtractedContent(
        url=url,
        text="article content",
        word_count=2,
        quality_passed=True,
        extractor=ExtractorName.TRAFILATURA,
    )
    webpage = ExtractedContent(
        url=url,
        text="webpage content",
        word_count=2,
        quality_passed=True,
        extractor=ExtractorName.PLAYWRIGHT,
    )

    _cache.clear()
    _cache.put(article_key, article)
    assert _cache.get(webpage_key) is None
    _cache.put(webpage_key, webpage)
    assert _cache.get(article_key) is article
    assert _cache.get(webpage_key) is webpage
    _cache.clear()


@pytest.mark.asyncio
async def test_accepted_operation_forwards_webpage_profile_to_builtin_extractor(
    monkeypatch,
):
    import argus.operations.accepted as accepted_module

    seen = {}

    async def extractor(url, **kwargs):
        seen.update(kwargs)
        return ExtractedContent(
            url=url,
            title="Webpage",
            text="visible webpage content",
            word_count=3,
            extractor=ExtractorName.PLAYWRIGHT,
        )

    monkeypatch.setattr(accepted_module, "extract_url", extractor)
    service = accepted_module.AcceptedOperationService(
        broker_provider=lambda: MagicMock(),
        repository_provider=lambda: MagicMock(),
    )

    operation = await service.extract(
        SimpleNamespace(
            url="https://example.com/page",
            domain=None,
            mode="default",
            caller="atlas",
            content_type="webpage",
        ),
        principal="atlas",
        request_id="request-webpage-profile",
    )

    assert operation.outcome.value == "success"
    assert seen["content_type"] == "webpage"
