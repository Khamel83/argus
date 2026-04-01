"""Tests for content extraction."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from argus.extraction.models import ExtractedContent, ExtractorName


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
        from argus.extraction.extractor import extract_url

        good_result = ExtractedContent(
            url="https://example.com",
            title="Title",
            text="Good content from trafilatura",
            word_count=5,
            extractor=ExtractorName.TRAFILATURA,
        )

        with patch("argus.extraction.extractor._extract_trafilatura", new_callable=AsyncMock, return_value=good_result):
            result = await extract_url("https://example.com")
            assert result.extractor == ExtractorName.TRAFILATURA
            assert "trafilatura" in result.text

    @pytest.mark.asyncio
    async def test_falls_back_to_jina(self):
        from argus.extraction.extractor import extract_url

        bad_result = ExtractedContent(url="https://example.com", error="no content")
        good_result = ExtractedContent(
            url="https://example.com",
            title="Title",
            text="Good content from Jina",
            word_count=5,
            extractor=ExtractorName.JINA,
        )

        with patch("argus.extraction.extractor._extract_trafilatura", new_callable=AsyncMock, return_value=bad_result):
            with patch("argus.extraction.extractor._extract_jina", new_callable=AsyncMock, return_value=good_result):
                result = await extract_url("https://example.com")
                assert result.extractor == ExtractorName.JINA

    @pytest.mark.asyncio
    async def test_all_extractors_fail(self):
        from argus.extraction.extractor import extract_url

        bad_result = ExtractedContent(url="https://example.com", error="failed")

        with patch("argus.extraction.extractor._extract_trafilatura", new_callable=AsyncMock, return_value=bad_result):
            with patch("argus.extraction.extractor._extract_jina", new_callable=AsyncMock, return_value=bad_result):
                result = await extract_url("https://example.com")
                assert result.error is not None
