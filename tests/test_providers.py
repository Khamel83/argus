"""Tests for provider adapters."""

import inspect
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from argus.config import ProviderConfig, SearXNGConfig
from argus.models import ProviderName, ProviderStatus, SearchMode, SearchQuery


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

    def test_valyu_not_available(self):
        from argus.providers.valyu import ValyuProvider
        p = ValyuProvider(ProviderConfig())
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

    @pytest.mark.asyncio
    async def test_valyu_returns_empty_when_disabled(self):
        from argus.providers.valyu import ValyuProvider
        p = ValyuProvider(ProviderConfig())
        results, trace = await p.search(SearchQuery(query="test"))
        assert results == []
        assert trace.status == "skipped"


# --- Valyu ---

class TestValyuProvider:
    def test_is_available_with_key(self):
        from argus.providers.valyu import ValyuProvider
        p = ValyuProvider(ProviderConfig(enabled=True, api_key="val_test_key"))
        assert p.is_available() is True

    def test_not_available_without_key(self):
        from argus.providers.valyu import ValyuProvider
        p = ValyuProvider(ProviderConfig(enabled=True, api_key=""))
        assert p.is_available() is False

    def test_status_missing_key(self):
        from argus.providers.valyu import ValyuProvider
        p = ValyuProvider(ProviderConfig(enabled=True, api_key=""))
        assert p.status() == ProviderStatus.UNAVAILABLE_MISSING_KEY

    def test_status_disabled(self):
        from argus.providers.valyu import ValyuProvider
        p = ValyuProvider(ProviderConfig(enabled=False))
        assert p.status() == ProviderStatus.DISABLED_BY_CONFIG

    @pytest.mark.asyncio
    async def test_search_normalizes_results(self):
        from argus.providers.valyu import ValyuProvider
        p = ValyuProvider(ProviderConfig(enabled=True, api_key="val_test_key"))

        mock_response = {
            "success": True,
            "tx_id": "tx_test123",
            "query": "test query",
            "results": [
                {
                    "id": "https://example.com",
                    "title": "Example",
                    "url": "https://example.com",
                    "content": "A test page with content",
                    "description": "A page",
                    "source": "web",
                    "price": 0.0015,
                    "length": 5000,
                    "relevance_score": 0.95,
                    "data_type": "unstructured",
                    "source_type": "website",
                },
                {"id": "", "title": "Empty URL", "url": "", "content": "skip me"},
            ],
            "results_by_source": {"web": 1},
            "total_deduction_dollars": 0.0015,
            "total_characters": 5000,
        }

        with patch("argus.providers.valyu.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.post = AsyncMock(return_value=_make_mock_response(mock_response))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            query = SearchQuery(query="test query")
            results, trace = await p.search(query)

        assert len(results) == 1
        assert results[0].url == "https://example.com"
        assert results[0].title == "Example"
        assert results[0].snippet == "A page"
        assert results[0].provider.value == "valyu"
        assert results[0].score == 0.95
        assert trace.status == "success"
        assert trace.results_count == 1
        assert trace.credit_info["cost_usd"] == 0.0015

    @pytest.mark.asyncio
    async def test_search_handles_api_error(self):
        from argus.providers.valyu import ValyuProvider
        p = ValyuProvider(ProviderConfig(enabled=True, api_key="val_test_key"))

        mock_response = {
            "success": False,
            "error": "Insufficient credits",
        }

        with patch("argus.providers.valyu.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.post = AsyncMock(return_value=_make_mock_response(mock_response))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            query = SearchQuery(query="test")
            results, trace = await p.search(query)

        assert results == []
        assert trace.status == "error"
        assert "Insufficient credits" in trace.error

    @pytest.mark.asyncio
    async def test_search_handles_connection_error(self):
        from argus.providers.valyu import ValyuProvider
        p = ValyuProvider(ProviderConfig(enabled=True, api_key="val_test_key"))

        with patch("argus.providers.valyu.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.post = AsyncMock(side_effect=Exception("connection refused"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            query = SearchQuery(query="test")
            results, trace = await p.search(query)

        assert results == []
        assert trace.status == "error"


# --- GitHub ---

class TestGitHubProvider:
    def test_is_available_when_enabled(self):
        from argus.providers.github import GitHubProvider
        p = GitHubProvider(ProviderConfig(enabled=True))
        assert p.is_available() is True

    def test_not_available_when_disabled(self):
        from argus.providers.github import GitHubProvider
        p = GitHubProvider(ProviderConfig(enabled=False))
        assert p.is_available() is False

    def test_status_disabled(self):
        from argus.providers.github import GitHubProvider
        p = GitHubProvider(ProviderConfig(enabled=False))
        assert p.status() == ProviderStatus.DISABLED_BY_CONFIG

    @pytest.mark.asyncio
    async def test_search_normalizes_results(self):
        from argus.providers.github import GitHubProvider
        p = GitHubProvider(ProviderConfig(enabled=True))

        mock_response = {
            "total_count": 1,
            "items": [
                {
                    "full_name": "Khamel83/argus",
                    "html_url": "https://github.com/Khamel83/argus",
                    "description": "Search broker for AI agents",
                    "stargazers_count": 42,
                    "language": "Python",
                    "forks_count": 5,
                    "topics": ["search", "mcp"],
                    "updated_at": "2026-04-13T00:00:00Z",
                },
            ],
        }

        with patch("argus.providers.github.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.get = AsyncMock(return_value=_make_mock_response(mock_response))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            query = SearchQuery(query="argus search broker")
            results, trace = await p.search(query)

        assert len(results) == 1
        assert results[0].url == "https://github.com/Khamel83/argus"
        assert results[0].title == "Khamel83/argus"
        assert results[0].snippet == "Search broker for AI agents"
        assert results[0].domain == "github.com"
        assert results[0].metadata["stars"] == 42
        assert trace.status == "success"

    @pytest.mark.asyncio
    async def test_search_handles_rate_limit(self):
        from argus.providers.github import GitHubProvider
        p = GitHubProvider(ProviderConfig(enabled=True))

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.json.return_value = {"message": "API rate limit exceeded"}
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("rate limit", request=MagicMock(), response=mock_resp)

        with patch("argus.providers.github.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            query = SearchQuery(query="test")
            results, trace = await p.search(query)

        assert results == []
        assert trace.status == "error"
        assert "rate limited" in trace.error


@pytest.mark.parametrize(
    ("provider_name", "factory"),
    [
        (ProviderName.SEARXNG, lambda: __import__("argus.providers.searxng", fromlist=["SearXNGProvider"]).SearXNGProvider(SearXNGConfig())),
        (ProviderName.BRAVE, lambda: __import__("argus.providers.brave", fromlist=["BraveProvider"]).BraveProvider(ProviderConfig(enabled=True, api_key="key"))),
        (ProviderName.SERPER, lambda: __import__("argus.providers.serper", fromlist=["SerperProvider"]).SerperProvider(ProviderConfig(enabled=True, api_key="key"))),
        (ProviderName.TAVILY, lambda: __import__("argus.providers.tavily", fromlist=["TavilyProvider"]).TavilyProvider(ProviderConfig(enabled=True, api_key="key"))),
        (ProviderName.EXA, lambda: __import__("argus.providers.exa", fromlist=["ExaProvider"]).ExaProvider(ProviderConfig(enabled=True, api_key="key"))),
        (ProviderName.SEARCHAPI, lambda: __import__("argus.providers.searchapi", fromlist=["SearchApiProvider"]).SearchApiProvider(ProviderConfig())),
        (ProviderName.YOU, lambda: __import__("argus.providers.you", fromlist=["YouProvider"]).YouProvider(ProviderConfig())),
        (ProviderName.VALYU, lambda: __import__("argus.providers.valyu", fromlist=["ValyuProvider"]).ValyuProvider(ProviderConfig())),
        (ProviderName.GITHUB, lambda: __import__("argus.providers.github", fromlist=["GitHubProvider"]).GitHubProvider(ProviderConfig(enabled=True))),
    ],
)
class TestProviderContracts:
    def test_implements_base_provider_contract(self, provider_name, factory):
        from argus.providers.base import BaseProvider

        provider = factory()

        assert isinstance(provider, BaseProvider)
        assert provider.name == provider_name
        assert isinstance(provider.status(), ProviderStatus)
        assert isinstance(provider.is_available(), bool)
        assert inspect.iscoroutinefunction(provider.search)
