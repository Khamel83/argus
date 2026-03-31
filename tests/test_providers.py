"""Tests for provider adapters."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from argus.config import ProviderConfig, SearXNGConfig
from argus.models import SearchMode, SearchQuery


def _make_mock_response(data):
    """Create a mock HTTP response with json() and raise_for_status."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = data
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def _mock_httpx(mock_get_or_post, response_data):
    """Patch httpx.AsyncClient to return mock response for get/post."""
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=_make_mock_response(response_data))
    mock_client.__aenter__.return_value.post = AsyncMock(return_value=_make_mock_response(response_data))
    mock_client.__aexit__.return_value = False
    return mock_client


# --- SearXNG ---

class TestSearXNGProvider:
    def test_is_available_when_enabled(self):
        from argus.providers.searxng import SearXNGProvider
        p = SearXNGProvider(SearXNGConfig(enabled=True, base_url="http://localhost:8080"))
        assert p.is_available() is True

    def test_not_available_when_disabled(self):
        from argus.providers.searxng import SearXNGProvider
        p = SearXNGProvider(SearXNGConfig(enabled=False))
        assert p.is_available() is False

    def test_name(self):
        from argus.providers.searxng import SearXNGProvider
        from argus.models import ProviderName
        p = SearXNGProvider(SearXNGConfig())
        assert p.name == ProviderName.SEARXNG

    @pytest.mark.asyncio
    async def test_search_normalizes_results(self):
        from argus.providers.searxng import SearXNGProvider
        p = SearXNGProvider(SearXNGConfig())

        mock_response = {
            "results": [
                {"url": "https://example.com", "title": "Example", "content": "A page", "engine": "google", "score": 1.5},
                {"url": "", "title": "Empty URL", "content": "skip me"},
                {"url": "https://other.com", "title": "Other", "content": "B page", "engines": ["bing", "google"]},
            ],
        }

        with patch("argus.providers.searxng.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.get = AsyncMock(return_value=_make_mock_response(mock_response))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            query = SearchQuery(query="test")
            results, trace = await p.search(query)

        assert len(results) == 2
        assert results[0].url == "https://example.com"
        assert results[0].provider.value == "searxng"
        assert results[0].snippet == "A page"
        assert trace.status == "success"
        assert trace.results_count == 2

    @pytest.mark.asyncio
    async def test_search_returns_error_trace_on_failure(self):
        from argus.providers.searxng import SearXNGProvider
        p = SearXNGProvider(SearXNGConfig())

        with patch("argus.providers.searxng.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.get = AsyncMock(side_effect=Exception("connection refused"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            query = SearchQuery(query="test")
            results, trace = await p.search(query)

        assert results == []
        assert trace.status == "error"
        assert "connection refused" in trace.error


# --- Brave ---

class TestBraveProvider:
    def test_is_available_with_key(self):
        from argus.providers.brave import BraveProvider
        p = BraveProvider(ProviderConfig(enabled=True, api_key="test-key"))
        assert p.is_available() is True

    def test_not_available_without_key(self):
        from argus.providers.brave import BraveProvider
        p = BraveProvider(ProviderConfig(enabled=True, api_key=""))
        assert p.is_available() is False

    def test_status_missing_key(self):
        from argus.providers.brave import BraveProvider
        from argus.models import ProviderStatus
        p = BraveProvider(ProviderConfig(enabled=True, api_key=""))
        assert p.status() == ProviderStatus.UNAVAILABLE_MISSING_KEY

    @pytest.mark.asyncio
    async def test_search_normalizes_web_results(self):
        from argus.providers.brave import BraveProvider
        p = BraveProvider(ProviderConfig(enabled=True, api_key="key"))

        mock_response = {
            "web": {
                "results": [
                    {"url": "https://brave.com", "title": "Brave", "description": "Browser", "age": "2 days"},
                ]
            }
        }

        with patch("argus.providers.brave.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.get = AsyncMock(return_value=_make_mock_response(mock_response))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            query = SearchQuery(query="brave")
            results, trace = await p.search(query)

        assert len(results) == 1
        assert results[0].url == "https://brave.com"
        assert results[0].snippet == "Browser"
        assert trace.status == "success"


# --- Serper ---

class TestSerperProvider:
    def test_is_available_with_key(self):
        from argus.providers.serper import SerperProvider
        p = SerperProvider(ProviderConfig(enabled=True, api_key="key"))
        assert p.is_available() is True

    @pytest.mark.asyncio
    async def test_search_normalizes_organic(self):
        from argus.providers.serper import SerperProvider
        p = SerperProvider(ProviderConfig(enabled=True, api_key="key"))

        mock_response = {
            "organic": [
                {"link": "https://google.com", "title": "Google", "snippet": "Search engine", "position": 1},
            ]
        }

        with patch("argus.providers.serper.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.post = AsyncMock(return_value=_make_mock_response(mock_response))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            query = SearchQuery(query="google")
            results, trace = await p.search(query)

        assert len(results) == 1
        assert results[0].url == "https://google.com"
        assert trace.status == "success"


# --- Tavily ---

class TestTavilyProvider:
    def test_is_available_with_key(self):
        from argus.providers.tavily import TavilyProvider
        p = TavilyProvider(ProviderConfig(enabled=True, api_key="key"))
        assert p.is_available() is True

    @pytest.mark.asyncio
    async def test_search_normalizes_results(self):
        from argus.providers.tavily import TavilyProvider
        p = TavilyProvider(ProviderConfig(enabled=True, api_key="key"))

        mock_response = {
            "results": [
                {"url": "https://tavily.com", "title": "Tavily", "content": "AI search", "score": 0.95},
            ]
        }

        with patch("argus.providers.tavily.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.post = AsyncMock(return_value=_make_mock_response(mock_response))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            query = SearchQuery(query="tavily")
            results, trace = await p.search(query)

        assert len(results) == 1
        assert results[0].score == 0.95


# --- Exa ---

class TestExaProvider:
    def test_is_available_with_key(self):
        from argus.providers.exa import ExaProvider
        p = ExaProvider(ProviderConfig(enabled=True, api_key="key"))
        assert p.is_available() is True

    @pytest.mark.asyncio
    async def test_search_normalizes_results(self):
        from argus.providers.exa import ExaProvider
        p = ExaProvider(ProviderConfig(enabled=True, api_key="key"))

        mock_response = {
            "results": [
                {"url": "https://exa.ai", "title": "Exa", "text": "Neural search", "score": 0.88, "id": "abc"},
            ]
        }

        with patch("argus.providers.exa.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.post = AsyncMock(return_value=_make_mock_response(mock_response))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            query = SearchQuery(query="exa")
            results, trace = await p.search(query)

        assert len(results) == 1
        assert results[0].url == "https://exa.ai"
        assert results[0].metadata["id"] == "abc"


# --- Stubs ---

class TestStubs:
    def test_searchapi_not_available(self):
        from argus.providers.searchapi import SearchApiProvider
        p = SearchApiProvider(ProviderConfig())
        assert p.is_available() is False

    def test_you_not_available(self):
        from argus.providers.you import YouProvider
        p = YouProvider(ProviderConfig())
        assert p.is_available() is False

    @pytest.mark.asyncio
    async def test_searchapi_returns_empty(self):
        from argus.providers.searchapi import SearchApiProvider
        p = SearchApiProvider(ProviderConfig())
        results, trace = await p.search(SearchQuery(query="test"))
        assert results == []
        assert trace.status == "skipped"

    @pytest.mark.asyncio
    async def test_you_returns_empty(self):
        from argus.providers.you import YouProvider
        p = YouProvider(ProviderConfig())
        results, trace = await p.search(SearchQuery(query="test"))
        assert results == []
        assert trace.status == "skipped"
