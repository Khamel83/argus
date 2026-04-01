"""Tests for content extraction."""

import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from argus.extraction.models import ExtractedContent, ExtractorName
from argus.core.cache import TTLCache, extraction_cache_key
from argus.extraction.rate_limit import DomainRateLimiter


class TestExtractionModels:
    def test_extracted_content_defaults(self):
        content = ExtractedContent(url="https://example.com")
        assert content.url == "https://example.com"
        assert content.title == ""
        assert content.text == ""
        assert content.word_count == 0
        assert content.extractor is None
        assert content.error is None

    def test_extracted_content_full(self):
        content = ExtractedContent(
            url="https://example.com/article",
            title="Test Article",
            text="This is the article body text.",
            author="John",
            date="2024-01-15",
            word_count=6,
            extractor=ExtractorName.TRAFILATURA,
        )
        assert content.word_count == 6
        assert content.extractor == ExtractorName.TRAFILATURA
        assert content.author == "John"


class TestTrafilaturaExtractor:
    @pytest.fixture(autouse=True)
    def _skip_without_trafilatura(self):
        pytest.importorskip("trafilatura")

    @pytest.mark.asyncio
    async def test_trafilatura_success(self):
        from argus.extraction.extractor import _extract_trafilatura

        with patch("trafilatura.fetch_url") as mock_fetch, \
             patch("trafilatura.bare_extraction") as mock_extract:
            mock_fetch.return_value = "<html><body><article><h1>Title</h1><p>Content here.</p></article></body></html>"
            mock_extract.return_value = {
                "text": "Content here.",
                "title": "Title",
                "author": "Author",
                "date": "2024-01-01",
            }

            result = await _extract_trafilatura("https://example.com")
            assert result.text == "Content here."
            assert result.title == "Title"
            assert result.extractor == ExtractorName.TRAFILATURA
            assert result.word_count == 2

    @pytest.mark.asyncio
    async def test_trafilatura_fetch_fails(self):
        from argus.extraction.extractor import _extract_trafilatura

        with patch("trafilatura.fetch_url") as mock_fetch:
            mock_fetch.return_value = None

            result = await _extract_trafilatura("https://example.com")
            assert result.error is not None
            assert "failed to fetch" in result.error

    @pytest.mark.asyncio
    async def test_trafilatura_no_content(self):
        from argus.extraction.extractor import _extract_trafilatura

        with patch("trafilatura.fetch_url") as mock_fetch, \
             patch("trafilatura.bare_extraction") as mock_extract:
            mock_fetch.return_value = "<html><body></body></html>"
            mock_extract.return_value = None

            result = await _extract_trafilatura("https://example.com")
            assert result.error is not None
            assert "no content" in result.error


class TestJinaExtractor:
    @pytest.mark.asyncio
    async def test_jina_success(self):
        from argus.extraction.extractor import _extract_jina

        mock_response = MagicMock()
        mock_response.text = "# Article Title\n\nThis is the article body content from Jina."
        mock_response.raise_for_status = MagicMock()

        with patch("argus.extraction.extractor.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await _extract_jina("https://example.com")
            assert result.title == "Article Title"
            assert "article body" in result.text
            assert result.extractor == ExtractorName.JINA

    @pytest.mark.asyncio
    async def test_jina_too_short(self):
        from argus.extraction.extractor import _extract_jina

        mock_response = MagicMock()
        mock_response.text = "too short"
        mock_response.raise_for_status = MagicMock()

        with patch("argus.extraction.extractor.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await _extract_jina("https://example.com")
            assert result.error is not None


class TestExtractUrl:
    @pytest.mark.asyncio
    async def test_trafilatura_primary_no_fallback(self):
        from argus.extraction.extractor import extract_url, _default_extractor

        _default_extractor._cache.clear()

        good_result = ExtractedContent(
            url="https://example.com",
            title="Title",
            text="Good content from trafilatura",
            word_count=5,
            extractor=ExtractorName.TRAFILATURA,
        )

        with patch("argus.extraction.extractor._extract_trafilatura", new_callable=AsyncMock, return_value=good_result), \
             patch.object(_default_extractor, "_get_sqlite_cached", return_value=None):
            result = await extract_url("https://example.com")
            assert result.extractor == ExtractorName.TRAFILATURA
            assert "trafilatura" in result.text

    @pytest.mark.asyncio
    async def test_falls_back_to_jina(self):
        from argus.extraction.extractor import extract_url, _default_extractor

        _default_extractor._cache.clear()
        _default_extractor._domain_limiter.clear()

        bad_result = ExtractedContent(url="https://example.com", error="no content")
        good_result = ExtractedContent(
            url="https://example.com",
            title="Title",
            text="Good content from Jina",
            word_count=5,
            extractor=ExtractorName.JINA,
        )

        with patch("argus.extraction.extractor._extract_trafilatura", new_callable=AsyncMock, return_value=bad_result), \
             patch("argus.extraction.extractor._extract_jina", new_callable=AsyncMock, return_value=good_result), \
             patch.object(_default_extractor, "_get_sqlite_cached", return_value=None), \
             patch.object(_default_extractor, "_save_to_sqlite"):
            result = await extract_url("https://example.com")
            assert result.extractor == ExtractorName.JINA

    @pytest.mark.asyncio
    async def test_all_extractors_fail(self):
        from argus.extraction.extractor import extract_url, _default_extractor

        _default_extractor._cache.clear()
        _default_extractor._domain_limiter.clear()

        bad_result = ExtractedContent(url="https://example.com", error="failed")

        with patch("argus.extraction.extractor._extract_trafilatura", new_callable=AsyncMock, return_value=bad_result), \
             patch("argus.extraction.extractor._extract_jina", new_callable=AsyncMock, return_value=bad_result), \
             patch.object(_default_extractor, "_get_sqlite_cached", return_value=None):
            result = await extract_url("https://example.com")
            assert result.error is not None


# --- Extraction Cache ---

class TestExtractionCache:
    def test_put_and_get(self):
        cache = TTLCache(ttl_seconds=3600, key_fn=extraction_cache_key,
                         skip_fn=lambda c: bool(c.error))
        content = ExtractedContent(
            url="https://example.com",
            title="Test",
            text="Content",
            word_count=1,
            extractor=ExtractorName.TRAFILATURA,
        )
        cache.put("https://example.com", value=content)
        result = cache.get("https://example.com")
        assert result is not None
        assert result.title == "Test"

    def test_cache_miss(self):
        cache = TTLCache(ttl_seconds=3600, key_fn=extraction_cache_key)
        assert cache.get("https://example.com/nonexistent") is None

    def test_cache_normalizes_url(self):
        cache = TTLCache(ttl_seconds=3600, key_fn=extraction_cache_key,
                         skip_fn=lambda c: bool(c.error))
        content = ExtractedContent(url="https://example.com", text="hi", word_count=1)
        cache.put("https://example.com/", value=content)
        assert cache.get("https://example.com") is not None

    def test_cache_ttl_expires(self):
        cache = TTLCache(ttl_seconds=0, key_fn=extraction_cache_key)
        content = ExtractedContent(url="https://example.com", text="hi", word_count=1)
        cache.put("https://example.com", value=content)
        time.sleep(0.01)
        assert cache.get("https://example.com") is None

    def test_cache_skips_errors(self):
        cache = TTLCache(ttl_seconds=3600, key_fn=extraction_cache_key,
                         skip_fn=lambda c: bool(c.error))
        content = ExtractedContent(url="https://example.com", error="failed")
        cache.put("https://example.com", value=content)
        assert cache.get("https://example.com") is None

    def test_cache_clear(self):
        cache = TTLCache(ttl_seconds=3600, key_fn=extraction_cache_key,
                         skip_fn=lambda c: bool(c.error))
        content = ExtractedContent(url="https://example.com", text="hi", word_count=1)
        cache.put("https://example.com", value=content)
        assert cache.size() == 1
        cache.clear()
        assert cache.size() == 0

    def test_cache_strips_trailing_slash(self):
        cache = TTLCache(ttl_seconds=3600, key_fn=extraction_cache_key,
                         skip_fn=lambda c: bool(c.error))
        content = ExtractedContent(url="https://example.com", text="hi", word_count=1)
        cache.put("https://example.com/", value=content)
        assert cache.get("https://example.com") is not None
        assert cache.get("https://example.com/") is not None


# --- Domain Rate Limiter ---

class TestDomainRateLimiter:
    def test_allows_within_limit(self):
        limiter = DomainRateLimiter(max_requests=3, window_seconds=60)
        for i in range(3):
            allowed, _ = limiter.is_allowed("https://example.com/page")
        assert allowed is True

    def test_blocks_over_limit(self):
        limiter = DomainRateLimiter(max_requests=2, window_seconds=60)
        limiter.is_allowed("https://example.com/page1")
        limiter.is_allowed("https://example.com/page2")
        allowed, retry_after = limiter.is_allowed("https://example.com/page3")
        assert allowed is False
        assert retry_after > 0

    def test_separate_domains_independent(self):
        limiter = DomainRateLimiter(max_requests=1, window_seconds=60)
        limiter.is_allowed("https://example.com/page1")
        allowed, _ = limiter.is_allowed("https://other.com/page1")
        assert allowed is True

    def test_window_expires(self):
        limiter = DomainRateLimiter(max_requests=1, window_seconds=0)
        limiter.is_allowed("https://example.com/page1")
        time.sleep(0.01)
        allowed, _ = limiter.is_allowed("https://example.com/page2")
        assert allowed is True

    def test_invalid_url_allowed(self):
        limiter = DomainRateLimiter(max_requests=1, window_seconds=60)
        allowed, _ = limiter.is_allowed("not-a-url")
        assert allowed is True

    def test_clear(self):
        limiter = DomainRateLimiter(max_requests=1, window_seconds=60)
        limiter.is_allowed("https://example.com/page1")
        limiter.clear()
        allowed, _ = limiter.is_allowed("https://example.com/page2")
        assert allowed is True


# --- Integration: extract_url with cache ---

class TestExtractUrlWithCache:
    @pytest.mark.asyncio
    async def test_cached_result_returned(self):
        from argus.extraction.extractor import extract_url, _default_extractor

        good_result = ExtractedContent(
            url="https://cached.example.com",
            title="Cached",
            text="From cache",
            word_count=2,
            extractor=ExtractorName.TRAFILATURA,
        )
        _default_extractor._cache.put("https://cached.example.com", value=good_result)

        with patch.object(_default_extractor, "_get_sqlite_cached", return_value=None):
            result = await extract_url("https://cached.example.com")
            assert result.title == "Cached"
            _default_extractor._cache.clear()

    @pytest.mark.asyncio
    async def test_domain_rate_limit_blocks(self):
        from argus.extraction.extractor import extract_url, _default_extractor

        for _ in range(10):
            _default_extractor._domain_limiter.is_allowed("https://limited.example.com/page")

        with patch.object(_default_extractor, "_get_sqlite_cached", return_value=None):
            result = await extract_url("https://limited.example.com/other")
            assert result.error is not None
            assert "rate limit" in result.error
            _default_extractor._domain_limiter.clear()
