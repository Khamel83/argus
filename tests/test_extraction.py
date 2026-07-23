"""Tests for content extraction."""

import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from argus.extraction.models import ExtractedContent, ExtractorName
from argus.extraction.cache import ExtractionCache
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

        mock_response = MagicMock()
        mock_response.text = "<html><body><article><h1>Title</h1><p>Content here.</p></article></body></html>"
        mock_response.url = "https://example.com"
        mock_response.raise_for_status = MagicMock()

        with patch("argus.extraction.extractor.httpx.AsyncClient") as mock_client_cls, \
             patch("trafilatura.bare_extraction") as mock_extract:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client
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
    async def test_trafilatura_document_result_is_normalized(self):
        from argus.extraction.extractor import _extract_trafilatura

        class DocumentLike:
            def as_dict(self):
                return {
                    "text": "Document shaped content.",
                    "title": None,
                    "author": None,
                    "date": "2026-07-22",
                }

        mock_response = MagicMock()
        mock_response.text = "<html><body><article>Content</article></body></html>"
        mock_response.url = "https://example.com"
        mock_response.raise_for_status = MagicMock()

        with patch("argus.extraction.extractor.httpx.AsyncClient") as mock_client_cls, \
             patch("trafilatura.bare_extraction", return_value=DocumentLike()):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await _extract_trafilatura("https://example.com")

        assert result.text == "Document shaped content."
        assert result.title == ""
        assert result.author == ""
        assert result.extractor == ExtractorName.TRAFILATURA

    @pytest.mark.asyncio
    async def test_trafilatura_fetch_fails(self):
        from argus.extraction.extractor import _extract_trafilatura

        mock_response = MagicMock()
        mock_response.text = ""
        mock_response.url = "https://example.com"
        mock_response.raise_for_status = MagicMock()

        with patch("argus.extraction.extractor.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await _extract_trafilatura("https://example.com")
            assert result.error is not None
            assert "failed to fetch" in result.error

    @pytest.mark.asyncio
    async def test_trafilatura_no_content(self):
        from argus.extraction.extractor import _extract_trafilatura

        mock_response = MagicMock()
        mock_response.text = "<html><body></body></html>"
        mock_response.url = "https://example.com"
        mock_response.raise_for_status = MagicMock()

        with patch("argus.extraction.extractor.httpx.AsyncClient") as mock_client_cls, \
             patch("trafilatura.bare_extraction") as mock_extract:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client
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


def _good_text(n: int = 150) -> str:
    """Generate text that passes the quality gate (n words)."""
    if n <= 1:
        return "word."
    return " ".join(["word"] * (n - 1) + ["done."])


# Failing result used to simulate extractor failures in chain tests
_BAD_RESULT = ExtractedContent(url="https://example.com", error="failed")

# Module paths for all chain extractors (order matters)
_CHAIN_EXTRACTORS = [
    ("auth", "argus.extraction.auth_extractor", "extract_authenticated"),
    ("trafilatura", "argus.extraction.extractor", "_extract_trafilatura"),
    ("crawl4ai", "argus.extraction.crawl4ai_extractor", "extract_crawl4ai"),
    ("obscura", "argus.extraction.obscura_extractor", "extract_obscura"),
    ("playwright", "argus.extraction.playwright_extractor", "extract_playwright"),
    ("residential", "argus.extraction.residential_extractor", "extract_residential"),
    ("jina", "argus.extraction.extractor", "_extract_jina"),
    ("valyu_contents", "argus.extraction.valyu_extractor", "extract_valyu_contents"),
    ("firecrawl", "argus.extraction.firecrawl_extractor", "extract_firecrawl"),
    ("you_contents", "argus.extraction.you_extractor", "extract_you_contents"),
    ("wayback", "argus.extraction.wayback_extractor", "extract_wayback"),
    ("archive_is", "argus.extraction.archive_extractor", "extract_archive_is"),
]


@pytest.fixture
def mock_chain():
    """Fixture that patches all chain extractors. Use parametrize or override."""
    patches = []
    mocks = {}

    for name, module_path, func_name in _CHAIN_EXTRACTORS:
        p = patch(f"{module_path}.{func_name}", new_callable=AsyncMock, return_value=_BAD_RESULT)
        m = p.start()
        patches.append(p)
        mocks[name] = m

    yield mocks

    for p in patches:
        p.stop()


class TestExtractUrl:
    @pytest.mark.asyncio
    async def test_trafilatura_primary_no_fallback(self, mock_chain):
        from argus.extraction.extractor import extract_url, _cache

        _cache.clear()

        good_result = ExtractedContent(
            url="https://example.com",
            title="Title",
            text=_good_text(150),
            word_count=150,
            extractor=ExtractorName.TRAFILATURA,
        )
        mock_chain["trafilatura"].return_value = good_result

        result = await extract_url("https://example.com")
        assert result.extractor == ExtractorName.TRAFILATURA
        assert result.quality_passed is True

    @pytest.mark.asyncio
    async def test_falls_back_to_jina(self, mock_chain):
        from argus.extraction.extractor import extract_url, _cache, _domain_limiter

        _cache.clear()
        _domain_limiter.clear()

        good_result = ExtractedContent(
            url="https://example.com",
            title="Title",
            text=_good_text(150),
            word_count=150,
            extractor=ExtractorName.JINA,
        )
        mock_chain["jina"].return_value = good_result

        result = await extract_url("https://example.com")
        assert result.extractor == ExtractorName.JINA

    @pytest.mark.asyncio
    async def test_all_extractors_fail(self, mock_chain):
        from argus.extraction.extractor import extract_url, _cache, _domain_limiter

        _cache.clear()
        _domain_limiter.clear()

        result = await extract_url("https://example.com")
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_ssrf_blocks_private_ip(self):
        from argus.extraction.extractor import extract_url

        result = await extract_url("http://192.168.1.1/admin")
        assert result.error is not None
        assert "ssrf_blocked" in result.error

    @pytest.mark.asyncio
    async def test_quality_gate_rejects_short_content(self, mock_chain):
        from argus.extraction.extractor import extract_url, _cache, _domain_limiter

        _cache.clear()
        _domain_limiter.clear()

        short_result = ExtractedContent(
            url="https://example.com",
            title="Title",
            text=_good_text(50),
            word_count=50,
            extractor=ExtractorName.TRAFILATURA,
        )
        good_result = ExtractedContent(
            url="https://example.com",
            title="Title",
            text=_good_text(200),
            word_count=200,
            extractor=ExtractorName.JINA,
        )
        mock_chain["trafilatura"].return_value = short_result
        mock_chain["jina"].return_value = good_result

        result = await extract_url("https://example.com")
        assert result.extractor == ExtractorName.JINA
        assert result.quality_passed is True
        assert result.attempts[0].status == "quality_failed"
        assert result.attempts[0].failure_summary

    @pytest.mark.asyncio
    async def test_quality_passed_incomplete_content_is_not_relabelled_as_quality_failed(
        self,
        mock_chain,
    ):
        """A completeness retry must not erase a successful quality evaluation."""
        from argus.extraction.extractor import extract_url, _cache, _domain_limiter

        _cache.clear()
        _domain_limiter.clear()

        # Sanitized substantial content: it passes the 100-word quality floor,
        # while the trailing ellipsis deliberately asks completeness to retry.
        incomplete_text = " ".join(f"word-{index}" for index in range(137)) + "..."
        mock_chain["trafilatura"].return_value = ExtractedContent(
            url="https://example.com/article",
            title="Sanitized fixture",
            text=incomplete_text,
            word_count=len(incomplete_text.split()),
            extractor=ExtractorName.TRAFILATURA,
        )

        result = await extract_url("https://example.com/article")

        assert result.extractor == ExtractorName.TRAFILATURA
        assert result.quality_passed is True
        assert result.quality_reason is None
        assert result.completeness_result is not None
        assert result.completeness_result.is_complete is False
        assert result.completeness_result.recommended_action == "try_full_fetch"
        assert mock_chain["archive_is"].await_count == 1
        assert result.attempts[-1].extractor == "archive_is"

    @pytest.mark.asyncio
    async def test_all_low_quality_content_remains_rejected(self, mock_chain):
        from argus.extraction.extractor import extract_url, _cache, _domain_limiter

        _cache.clear()
        _domain_limiter.clear()

        mock_chain["trafilatura"].return_value = ExtractedContent(
            url="https://example.com/article",
            title="Sanitized short fixture",
            text=_good_text(50),
            word_count=50,
            extractor=ExtractorName.TRAFILATURA,
        )

        result = await extract_url("https://example.com/article")

        assert result.extractor == ExtractorName.TRAFILATURA
        assert result.quality_passed is False
        assert result.quality_reason == "all_extractors_quality_failed"

    @pytest.mark.asyncio
    async def test_extractors_tried_tracked(self, mock_chain):
        from argus.extraction.extractor import extract_url, _cache, _domain_limiter

        _cache.clear()
        _domain_limiter.clear()

        result = await extract_url("https://example.com")
        assert "trafilatura" in result.extractors_tried

    @pytest.mark.asyncio
    async def test_attempt_outcomes_and_latency_are_recorded(self, mock_chain):
        from argus.extraction.extractor import extract_url, _cache, _domain_limiter

        _cache.clear()
        _domain_limiter.clear()
        good_result = ExtractedContent(
            url="https://example.com",
            title="Title",
            text=_good_text(150),
            word_count=150,
            extractor=ExtractorName.JINA,
        )
        mock_chain["jina"].return_value = good_result

        result = await extract_url("https://example.com")

        assert result.attempts
        assert result.attempts[-1].extractor == "jina"
        assert result.attempts[-1].status == "success"
        assert all(attempt.latency_ms >= 0 for attempt in result.attempts)
        assert result.attempts[0].failure_summary

    @pytest.mark.asyncio
    async def test_enabled_extraction_chain_order(self, mock_chain, monkeypatch):
        from argus.extraction.extractor import extract_url, _cache, _domain_limiter

        monkeypatch.setenv("ARGUS_CRAWL4AI_ENABLED", "true")
        monkeypatch.setenv("ARGUS_YOU_CONTENTS_ENABLED", "true")
        monkeypatch.setenv("ARGUS_RESIDENTIAL_POLICY", "fallback")
        monkeypatch.setattr("argus.extraction.residential_extractor._is_configured", lambda: True)
        _cache.clear()
        _domain_limiter.clear()

        result = await extract_url("https://example.com", domain="nytimes.com")

        assert result.extractors_tried == [
            "auth",
            "trafilatura",
            "crawl4ai",
            "obscura",
            "playwright",
            "residential",
            "jina",
            "valyu_contents",
            "firecrawl",
            "you_contents",
            "wayback",
            "archive_is",
        ]


# --- Extraction Cache ---

class TestExtractionCache:
    def test_put_and_get(self):
        cache = ExtractionCache(ttl_hours=1)
        content = ExtractedContent(
            url="https://example.com",
            title="Test",
            text="Content",
            word_count=1,
            extractor=ExtractorName.TRAFILATURA,
        )
        cache.put("https://example.com", content)
        result = cache.get("https://example.com")
        assert result is not None
        assert result.title == "Test"

    def test_cache_miss(self):
        cache = ExtractionCache()
        assert cache.get("https://example.com/nonexistent") is None

    def test_cache_normalizes_url(self):
        cache = ExtractionCache(ttl_hours=1)
        content = ExtractedContent(url="https://example.com", text="hi", word_count=1)
        cache.put("https://example.com/", content)
        assert cache.get("https://example.com") is not None

    def test_cache_ttl_expires(self):
        cache = ExtractionCache(ttl_hours=0)  # 0 hours = immediate expiry
        content = ExtractedContent(url="https://example.com", text="hi", word_count=1)
        cache.put("https://example.com", content)
        # TTL is 0 * 3600 = 0 seconds, so even immediate check should miss
        time.sleep(0.01)
        assert cache.get("https://example.com") is None

    def test_cache_skips_errors(self):
        cache = ExtractionCache()
        content = ExtractedContent(url="https://example.com", error="failed")
        cache.put("https://example.com", content)
        assert cache.get("https://example.com") is None

    def test_cache_clear(self):
        cache = ExtractionCache()
        content = ExtractedContent(url="https://example.com", text="hi", word_count=1)
        cache.put("https://example.com", content)
        assert cache.size() == 1
        cache.clear()
        assert cache.size() == 0

    def test_cache_strips_trailing_slash(self):
        cache = ExtractionCache(ttl_hours=1)
        content = ExtractedContent(url="https://example.com", text="hi", word_count=1)
        cache.put("https://example.com/", content)
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
        from argus.extraction.extractor import extract_url, _cache

        good_result = ExtractedContent(
            url="https://cached.example.com",
            title="Cached",
            text="From cache",
            word_count=2,
            extractor=ExtractorName.TRAFILATURA,
        )
        _cache.put("https://cached.example.com", good_result)

        # extract_url should return cached result without calling any extractor
        result = await extract_url("https://cached.example.com")
        assert result.title == "Cached"
        _cache.clear()

    @pytest.mark.asyncio
    async def test_domain_rate_limit_blocks(self):
        from argus.extraction.extractor import extract_url, _domain_limiter

        # Fill up the rate limit for this domain
        for _ in range(10):
            _domain_limiter.is_allowed("https://limited.example.com/page")

        # Next request should be blocked
        result = await extract_url("https://limited.example.com/other")
        assert result.error is not None
        assert "rate limit" in result.error
        _domain_limiter.clear()
