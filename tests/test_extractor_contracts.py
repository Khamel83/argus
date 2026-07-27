"""Fixture-only compatibility tests for drifting extractor result shapes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from argus.extraction.crawl4ai_extractor import normalize_crawl4ai_result
from argus.extraction.firecrawl_extractor import (
    FIRECRAWL_API_URL,
    extract_firecrawl,
    parse_firecrawl_v2_response,
)
from argus.extraction.models import ExtractorName

FIXTURES = Path(__file__).parent / "fixtures" / "extractors"


@pytest.fixture(autouse=True)
def _prohibit_extractor_contract_dns(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("extractor contract tests must not use DNS")

    monkeypatch.setattr("socket.getaddrinfo", fail)
    monkeypatch.setattr("socket.create_connection", fail)


def _load(*parts: str) -> object:
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="utf-8"))


def test_locked_crawl4ai_markdown_object_and_legacy_string_are_supported():
    current = normalize_crawl4ai_result(
        "https://example.test/article",
        _load("crawl4ai", "markdown_object_success.json"),
    )
    legacy = normalize_crawl4ai_result(
        "https://example.test/article",
        _load("crawl4ai", "legacy_string_success.json"),
    )
    assert current.extractor is ExtractorName.CRAWL4AI
    assert current.text.startswith("# Fixture")
    assert current.title == "Fixture title"
    assert legacy.text.startswith("# Legacy")


def test_crawl4ai_malformed_or_unsuccessful_shape_fails_closed():
    for fixture in ("malformed.json", "error.json", "missing_success.json"):
        outcome = normalize_crawl4ai_result(
            "https://example.test/article",
            _load("crawl4ai", fixture),
        )
        assert outcome.error
        assert "raw body sentinel" not in outcome.error


def test_firecrawl_v2_success_and_error_shapes_are_bounded():
    success = parse_firecrawl_v2_response(
        "https://example.test/article",
        _load("firecrawl", "v2_success.json"),
    )
    error = parse_firecrawl_v2_response(
        "https://example.test/article",
        _load("firecrawl", "v2_error.json"),
    )
    assert FIRECRAWL_API_URL.endswith("/v2/scrape")
    assert success.extractor is ExtractorName.FIRECRAWL
    assert success.title == "Firecrawl fixture"
    metadata_error = parse_firecrawl_v2_response(
        "https://example.test/article",
        _load("firecrawl", "v2_metadata_error.json"),
    )
    assert error.error == "firecrawl: extraction failed"
    assert metadata_error.error == "firecrawl: extraction failed"
    assert "raw body sentinel" not in metadata_error.error


def test_crawl4ai_redirect_fixture_is_hermetic_and_dns_is_never_used():
    with (
        patch(
            "argus.extraction.crawl4ai_extractor.is_safe_url",
            return_value=(True, ""),
        ) as safe_url,
        patch("socket.getaddrinfo", side_effect=AssertionError("DNS prohibited")),
    ):
        result = normalize_crawl4ai_result(
            "https://example.test/article",
            _load("crawl4ai", "redirect_success.json"),
        )
    assert result.url == "https://redirected.example.test/article"
    safe_url.assert_called_once_with("https://redirected.example.test/article")


@pytest.mark.asyncio
async def test_firecrawl_remains_disabled_without_spend_authorization_and_never_calls_network():
    with patch("argus.extraction.firecrawl_extractor.httpx.AsyncClient") as client:
        outcome = await extract_firecrawl("https://example.test/article")
    assert "durable spend reservation is required" in outcome.error
    client.assert_not_called()
