"""Tests for HTTP API endpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from argus.api.schemas import SearchRequest, SearchResponse, RecoverUrlRequest, ExpandRequest, ProviderTestRequest


# --- Schemas ---

class TestSchemas:
    def test_search_request_valid(self):
        req = SearchRequest(query="test", mode="discovery", max_results=10)
        assert req.query == "test"
        assert req.mode == "discovery"

    def test_search_request_invalid_mode(self):
        with pytest.raises(Exception):
            SearchRequest(query="test", mode="invalid")

    def test_search_request_min_length(self):
        with pytest.raises(Exception):
            SearchRequest(query="")

    def test_search_result_schema(self):
        from argus.api.schemas import SearchResultSchema
        r = SearchResultSchema(url="https://example.com", title="Test", snippet="A page")
        assert r.url == "https://example.com"
        assert r.score == 0.0

    def test_recover_url_request(self):
        req = RecoverUrlRequest(url="https://example.com")
        assert req.url == "https://example.com"

    def test_expand_request(self):
        req = ExpandRequest(query="python", context="web framework")
        assert req.query == "python"

    def test_test_provider_request(self):
        req = ProviderTestRequest(provider="searxng")
        assert req.provider == "searxng"


# --- API Integration ---

class TestSearchEndpoint:
    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        from argus.api.main import app
        from argus.api.routes_search import get_broker
        from argus.broker.cache import SearchCache
        from argus.broker.health import HealthTracker
        from argus.broker.budgets import BudgetTracker
        from argus.models import SearchResponse, SearchMode, SearchResult, ProviderTrace

        # Create a mock broker with a cache hit
        mock_broker = MagicMock()
        cached_resp = SearchResponse(
            query="test",
            mode=SearchMode.DISCOVERY,
            results=[SearchResult(url="https://example.com", title="Test", snippet="A page")],
            traces=[],
            total_results=1,
            cached=True,
            search_run_id="abc123",
        )
        mock_broker.search = AsyncMock(return_value=cached_resp)
        mock_broker.cache = SearchCache()
        mock_broker.health_tracker = HealthTracker()
        mock_broker.budget_tracker = BudgetTracker()

        with patch("argus.api.routes_search.get_broker", return_value=mock_broker):
            from fastapi.testclient import TestClient
            client = TestClient(app)

            resp = client.post("/api/search", json={"query": "test", "mode": "discovery"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["query"] == "test"
            assert data["mode"] == "discovery"
            assert len(data["results"]) == 1
            assert data["results"][0]["url"] == "https://example.com"
            assert data["cached"] is True

    @pytest.mark.asyncio
    async def test_search_invalid_mode_returns_400(self):
        from argus.api.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post("/api/search", json={"query": "test", "mode": "invalid_mode"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_recover_url_endpoint(self):
        from argus.api.main import app
        from argus.broker.router import SearchBroker
        from argus.models import SearchResponse, SearchMode

        mock_broker = MagicMock()
        mock_broker.search = AsyncMock(return_value=SearchResponse(
            query="https://dead.com Page Title",
            mode=SearchMode.RECOVERY,
            results=[],
            total_results=0,
            cached=False,
            search_run_id="xyz",
        ))
        mock_broker.cache = MagicMock()
        mock_broker.health_tracker = MagicMock()
        mock_broker.budget_tracker = MagicMock()

        with patch("argus.api.routes_search.get_broker", return_value=mock_broker):
            from fastapi.testclient import TestClient
            client = TestClient(app)

            resp = client.post("/api/recover-url", json={"url": "https://dead.com", "title": "Page Title"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["mode"] == "recovery"

    @pytest.mark.asyncio
    async def test_expand_endpoint(self):
        from argus.api.main import app
        from argus.broker.router import SearchBroker
        from argus.models import SearchResponse, SearchMode

        mock_broker = MagicMock()
        mock_broker.search = AsyncMock(return_value=SearchResponse(
            query="python web framework",
            mode=SearchMode.DISCOVERY,
            results=[],
            total_results=0,
            cached=False,
            search_run_id="exp1",
        ))
        mock_broker.cache = MagicMock()
        mock_broker.health_tracker = MagicMock()
        mock_broker.budget_tracker = MagicMock()

        with patch("argus.api.routes_search.get_broker", return_value=mock_broker):
            from fastapi.testclient import TestClient
            client = TestClient(app)

            resp = client.post("/api/expand", json={"query": "python", "context": "web framework"})
            assert resp.status_code == 200


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        from argus.api.main import app

        mock_broker = MagicMock()
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})

        with patch("argus.api.routes_health.get_broker", return_value=mock_broker):
            from fastapi.testclient import TestClient
            client = TestClient(app)

            resp = client.get("/api/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"


class TestRequestCorrelation:
    @pytest.mark.asyncio
    async def test_x_request_id_header(self):
        from argus.api.main import app

        mock_broker = MagicMock()
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})

        with patch("argus.api.routes_health.get_broker", return_value=mock_broker):
            from fastapi.testclient import TestClient
            client = TestClient(app)

            resp = client.get("/api/health", headers={"x-request-id": "test-123"})
            assert resp.headers.get("x-request-id") == "test-123"

    @pytest.mark.asyncio
    async def test_auto_generated_request_id(self):
        from argus.api.main import app

        mock_broker = MagicMock()
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})

        with patch("argus.api.routes_health.get_broker", return_value=mock_broker):
            from fastapi.testclient import TestClient
            client = TestClient(app)

            resp = client.get("/api/health")
            assert "x-request-id" in resp.headers
            assert len(resp.headers["x-request-id"]) == 16
