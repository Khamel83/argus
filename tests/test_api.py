"""Tests for HTTP API endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from argus.api.schemas import SearchRequest, RecoverUrlRequest, ExpandRequest, ProviderTestRequest


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
        from argus.api.main import create_app
        from argus.broker.cache import SearchCache
        from argus.broker.health import HealthTracker
        from argus.broker.budgets import BudgetTracker
        from argus.models import SearchResponse, SearchMode, SearchResult

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

        from fastapi.testclient import TestClient
        client = TestClient(create_app(broker=mock_broker))

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
        from argus.api.main import create_app
        from fastapi.testclient import TestClient

        client = TestClient(create_app())
        resp = client.post("/api/search", json={"query": "test", "mode": "invalid_mode"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_recover_url_endpoint(self):
        from argus.api.main import create_app
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

        from fastapi.testclient import TestClient
        client = TestClient(create_app(broker=mock_broker))

        resp = client.post("/api/recover-url", json={"url": "https://dead.com", "title": "Page Title"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "recovery"

    @pytest.mark.asyncio
    async def test_expand_endpoint(self):
        from argus.api.main import create_app
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

        from fastapi.testclient import TestClient
        client = TestClient(create_app(broker=mock_broker))

        resp = client.post("/api/expand", json={"query": "python", "context": "web framework"})
        assert resp.status_code == 200

    def test_create_app_uses_lazy_singleton_broker_factory(self):
        from argus.api.main import create_app

        broker = MagicMock()
        broker_factory = MagicMock(return_value=broker)
        app = create_app(broker_factory=broker_factory)

        assert app.state.get_broker() is broker
        assert app.state.get_broker() is broker
        assert broker_factory.call_count == 1


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        from argus.api.main import create_app

        mock_broker = MagicMock()
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})

        from fastapi.testclient import TestClient
        client = TestClient(create_app(broker=mock_broker))

        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_detail_moved_under_admin_prefix(self):
        from argus.api.main import create_app
        from fastapi.testclient import TestClient

        mock_broker = MagicMock()
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})
        mock_broker.health_tracker.get_all_status = MagicMock(return_value={})

        client = TestClient(create_app(broker=mock_broker))

        resp = client.get("/api/health/detail")
        assert resp.status_code == 404


class TestRequestCorrelation:
    @pytest.mark.asyncio
    async def test_x_request_id_header(self):
        from argus.api.main import create_app

        mock_broker = MagicMock()
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})

        from fastapi.testclient import TestClient
        client = TestClient(create_app(broker=mock_broker))

        resp = client.get("/api/health", headers={"x-request-id": "test-123"})
        assert resp.headers.get("x-request-id") == "test-123"

    @pytest.mark.asyncio
    async def test_auto_generated_request_id(self):
        from argus.api.main import create_app

        mock_broker = MagicMock()
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})

        from fastapi.testclient import TestClient
        client = TestClient(create_app(broker=mock_broker))

        resp = client.get("/api/health")
        assert "x-request-id" in resp.headers
        assert len(resp.headers["x-request-id"]) == 16


class TestRateLimitComposition:
    def test_rate_limit_headers_and_enforcement(self):
        from fastapi.testclient import TestClient

        from argus.api.main import create_app
        from argus.api.rate_limit import RateLimiter
        from argus.models import SearchMode, SearchResponse

        mock_broker = MagicMock()
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})
        mock_broker.search = AsyncMock(
            return_value=SearchResponse(
                query="test",
                mode=SearchMode.DISCOVERY,
                results=[],
                total_results=0,
                cached=False,
                search_run_id="rl-1",
            )
        )

        limiter = RateLimiter(max_requests=1, window_seconds=60, exempt_paths=[], exempt_tokens=[])
        client = TestClient(create_app(broker=mock_broker, rate_limiter=limiter))

        first = client.post("/api/search", json={"query": "test", "mode": "discovery"})
        second = client.post("/api/search", json={"query": "test", "mode": "discovery"})

        assert first.status_code == 200
        assert "X-RateLimit-Limit" in first.headers
        assert second.status_code == 429
        assert second.json()["error"] == "Rate limit exceeded"


class TestAuthEnforcement:
    def _build_broker(self):
        from argus.broker.budgets import BudgetTracker
        from argus.broker.health import HealthTracker
        from argus.models import SearchMode, SearchResponse

        mock_broker = MagicMock()
        mock_broker.search = AsyncMock(
            return_value=SearchResponse(
                query="test",
                mode=SearchMode.DISCOVERY,
                results=[],
                total_results=0,
                cached=False,
                search_run_id="auth-1",
            )
        )
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})
        mock_broker.health_tracker = HealthTracker()
        mock_broker.budget_tracker = BudgetTracker()
        return mock_broker

    def test_remote_search_requires_api_key(self, monkeypatch):
        from fastapi.testclient import TestClient

        from argus.api.main import create_app

        monkeypatch.setenv("ARGUS_API_KEY", "caller-secret")
        client = TestClient(create_app(broker=self._build_broker()), client=("203.0.113.10", 50000))

        resp = client.post("/api/search", json={"query": "test", "mode": "discovery"})
        assert resp.status_code == 401
        assert resp.json()["error"] == "Authentication required"

    def test_remote_search_accepts_bearer_or_x_api_key(self, monkeypatch):
        from fastapi.testclient import TestClient

        from argus.api.main import create_app

        monkeypatch.setenv("ARGUS_API_KEY", "caller-secret")
        client = TestClient(create_app(broker=self._build_broker()), client=("203.0.113.10", 50000))

        bearer = client.post(
            "/api/search",
            json={"query": "test", "mode": "discovery"},
            headers={"Authorization": "Bearer caller-secret"},
        )
        x_api_key = client.post(
            "/api/search",
            json={"query": "test", "mode": "discovery"},
            headers={"X-API-Key": "caller-secret"},
        )

        assert bearer.status_code == 200
        assert x_api_key.status_code == 200

    def test_remote_search_fails_when_api_key_not_configured(self, monkeypatch):
        from fastapi.testclient import TestClient

        from argus.api.main import create_app

        monkeypatch.delenv("ARGUS_API_KEY", raising=False)
        monkeypatch.delenv("ARGUS_ADMIN_API_KEY", raising=False)
        client = TestClient(create_app(broker=self._build_broker()), client=("203.0.113.10", 50000))

        resp = client.post("/api/search", json={"query": "test", "mode": "discovery"})
        assert resp.status_code == 503
        assert resp.json()["error"] == "API key is not configured for remote access"

    def test_admin_route_requires_admin_key_even_for_local_client(self, monkeypatch):
        from fastapi.testclient import TestClient

        from argus.api.main import create_app

        monkeypatch.setenv("ARGUS_API_KEY", "caller-secret")
        monkeypatch.setenv("ARGUS_ADMIN_API_KEY", "admin-secret")
        client = TestClient(create_app(broker=self._build_broker()))

        resp = client.get("/api/admin/health/detail")
        assert resp.status_code == 401
        assert resp.json()["error"] == "Admin authentication required"

    def test_admin_route_accepts_admin_token(self, monkeypatch):
        from fastapi.testclient import TestClient

        from argus.api.main import create_app

        monkeypatch.setenv("ARGUS_API_KEY", "caller-secret")
        monkeypatch.setenv("ARGUS_ADMIN_API_KEY", "admin-secret")
        client = TestClient(create_app(broker=self._build_broker()))

        resp = client.get(
            "/api/admin/health/detail",
            headers={"X-Admin-API-Key": "admin-secret"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestWorkflowEndpoints:
    def _workflow_run(self):
        from argus.workflows.models import WorkflowKind, WorkflowResult, WorkflowStatus

        return WorkflowResult(
            run_id="wf-1",
            kind=WorkflowKind.RECOVER_ARTICLE,
            status=WorkflowStatus.RUNNING,
            target="https://dead.example.com/post",
            status_url="/api/workflows/wf-1",
            snapshot_dir="/tmp/argus/snapshots/recover-article/dead-example-com/wf-1",
        )

    def test_admin_paths_endpoint(self, monkeypatch):
        from fastapi.testclient import TestClient

        from argus.api.main import create_app

        mock_broker = MagicMock()
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})
        mock_broker.health_tracker.get_all_status = MagicMock(return_value={})
        mock_broker.budget_tracker = MagicMock()

        mock_workflows = MagicMock()
        mock_workflows.get_paths.return_value = {
            "data_root": "/tmp/argus",
            "docs_root": "/tmp/argus/docs",
            "docs_cache_dir": "/tmp/argus/docs/cache",
            "docs_cache_index": "/tmp/argus/docs/cache/.index.md",
            "research_dir": "/tmp/argus/docs/research",
            "workflow_runs_dir": "/tmp/argus/workflows/runs",
            "snapshots_dir": "/tmp/argus/snapshots",
            "imports_dir": "/tmp/argus/imports",
            "env_override": None,
            "uses_platformdirs": False,
        }

        monkeypatch.setenv("ARGUS_ADMIN_API_KEY", "admin-secret")
        app = create_app(broker=mock_broker)
        app.state.get_workflows = lambda: mock_workflows
        client = TestClient(app)

        resp = client.get("/api/admin/paths", headers={"X-Admin-API-Key": "admin-secret"})
        assert resp.status_code == 200
        assert resp.json()["data_root"] == "/tmp/argus"

    def test_recover_article_endpoint_starts_run(self):
        from fastapi.testclient import TestClient

        from argus.api.main import create_app

        mock_broker = MagicMock()
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})
        mock_broker.health_tracker.get_all_status = MagicMock(return_value={})
        mock_broker.budget_tracker = MagicMock()

        mock_workflows = MagicMock()
        mock_workflows.start_recover_article = AsyncMock(return_value=self._workflow_run())
        mock_workflows.get_run = MagicMock(return_value=self._workflow_run())

        app = create_app(broker=mock_broker)
        app.state.get_workflows = lambda: mock_workflows
        client = TestClient(app)

        resp = client.post("/api/workflows/recover-article", json={"url": "https://dead.example.com/post"})
        assert resp.status_code == 200
        assert resp.json()["run_id"] == "wf-1"
        assert resp.json()["status"] == "running"

    def test_workflow_status_endpoint(self):
        from fastapi.testclient import TestClient

        from argus.api.main import create_app

        mock_broker = MagicMock()
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})
        mock_broker.health_tracker.get_all_status = MagicMock(return_value={})
        mock_broker.budget_tracker = MagicMock()

        mock_workflows = MagicMock()
        mock_workflows.get_run = MagicMock(return_value=self._workflow_run())

        app = create_app(broker=mock_broker)
        app.state.get_workflows = lambda: mock_workflows
        client = TestClient(app)

        resp = client.get("/api/workflows/wf-1")
        assert resp.status_code == 200
        assert resp.json()["status_url"] == "/api/workflows/wf-1"
