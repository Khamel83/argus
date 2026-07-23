"""Tests for HTTP API endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from argus.api.schemas import SearchRequest, RecoverUrlRequest, ExpandRequest, ProviderTestRequest


@pytest.fixture(autouse=True)
def isolated_api_ledger(tmp_path, monkeypatch):
    from argus.config import reset_config

    monkeypatch.setenv("ARGUS_DB_URL", f"sqlite:///{tmp_path / 'api-ledger.db'}")
    reset_config()
    yield
    reset_config()


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


@pytest.mark.asyncio
async def test_admin_provider_smoke_marks_query_operational_only():
    from argus.api.routes_admin import test_provider
    from argus.models import SearchMode, SearchResponse

    broker = MagicMock()
    broker.search = AsyncMock(
        return_value=SearchResponse(
            query="argus",
            mode=SearchMode.DISCOVERY,
            results=[],
        )
    )
    request = MagicMock()
    request.state.caller_identity = "admin"

    await test_provider(
        ProviderTestRequest(provider="duckduckgo", query="argus"),
        request,
        broker,
    )

    assert broker.search.await_args.args[0].user_visible is False


# --- API Integration ---

class TestSearchEndpoint:
    def test_real_broker_ledger_failure_leaves_no_legacy_completed_run(
        self,
        tmp_path,
    ):
        from fastapi.testclient import TestClient
        from sqlalchemy import func, select

        from argus.api.main import create_app
        from argus.broker.router import SearchBroker
        from argus.models import (
            ProviderName,
            ProviderStatus,
            ProviderTrace,
            SearchResult,
        )
        from argus.persistence.db import get_session_factory, init_db
        from argus.persistence.models import SearchRunRow

        class StubProvider:
            name = ProviderName.DUCKDUCKGO

            def is_available(self):
                return True

            def status(self):
                return ProviderStatus.ENABLED

            async def search(self, query):
                return (
                    [
                        SearchResult(
                            url="https://example.com/not-accepted",
                            title="Not accepted",
                            snippet="Must not become a legacy completed run",
                            provider=self.name,
                        )
                    ],
                    ProviderTrace(
                        provider=self.name,
                        status="success",
                        results_count=1,
                    ),
                )

        class FailingRepository:
            def accept(self, query, response):
                raise RuntimeError("commit failed")

        legacy_path = tmp_path / "legacy-completed.db"
        init_db(f"sqlite:///{legacy_path}")
        broker = SearchBroker(providers={ProviderName.DUCKDUCKGO: StubProvider()})
        client = TestClient(
            create_app(broker=broker, search_repository=FailingRepository())
        )

        response = client.post(
            "/api/search",
            json={
                "query": "must be atomically accepted",
                "mode": "discovery",
                "providers": ["duckduckgo"],
            },
        )

        assert response.status_code == 503
        with get_session_factory()() as session:
            assert session.scalar(select(func.count()).select_from(SearchRunRow)) == 0

    def test_postgresql_constraint_failure_returns_503_and_rolls_back_ledger(
        self,
        migrated_postgres_ledger,
    ):
        from fastapi.testclient import TestClient
        from sqlalchemy import func, select

        from argus.api.main import create_app
        from argus.models import (
            ProviderName,
            ProviderTrace,
            SearchMode,
            SearchResponse,
            SearchResult,
        )
        from argus.persistence.search_ledger import (
            ContentIdentityRow,
            DeliveryIntentRow,
            NormalizedResultRow,
            ProviderAttemptRow,
            ResultProvenanceRow,
            RetrievalRequestRow,
            RetrievalRunRow,
        )

        broker = MagicMock()
        broker.search = AsyncMock(
            return_value=SearchResponse(
                query="postgres commit failure",
                mode=SearchMode.DISCOVERY,
                results=[
                    SearchResult(
                        url="https://example.com/postgres-failure",
                        title="PostgreSQL failure",
                        snippet="Must not be acknowledged",
                        provider=ProviderName.DUCKDUCKGO,
                    )
                ],
                traces=[
                    ProviderTrace(
                        provider=ProviderName.DUCKDUCKGO,
                        status="success",
                        results_count=1,
                        egress="x" * 51,
                    )
                ],
                total_results=1,
                search_run_id="postgres-api-db-error",
            )
        )
        broker.cache = MagicMock()
        broker.health_tracker = MagicMock()
        broker.budget_tracker = MagicMock()
        client = TestClient(
            create_app(
                broker=broker,
                search_repository=migrated_postgres_ledger,
            )
        )

        response = client.post(
            "/api/search",
            json={"query": "postgres commit failure", "mode": "discovery"},
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "Search could not be durably accepted"
        tables = (
            RetrievalRequestRow,
            RetrievalRunRow,
            ProviderAttemptRow,
            NormalizedResultRow,
            ResultProvenanceRow,
            ContentIdentityRow,
            DeliveryIntentRow,
        )
        with migrated_postgres_ledger.session_factory() as session:
            assert all(
                session.scalar(select(func.count()).select_from(table)) == 0
                for table in tables
            )

    @pytest.mark.asyncio
    async def test_search_does_not_acknowledge_when_ledger_commit_fails(self):
        from argus.api.main import create_app
        from argus.models import SearchMode, SearchResponse

        class FailingRepository:
            def accept(self, query, response):
                raise RuntimeError("commit failed")

        mock_broker = MagicMock()
        mock_broker.search = AsyncMock(return_value=SearchResponse(
            query="test",
            mode=SearchMode.DISCOVERY,
            results=[],
            total_results=0,
            search_run_id="failed-ledger",
        ))
        mock_broker.cache = MagicMock()
        mock_broker.health_tracker = MagicMock()
        mock_broker.budget_tracker = MagicMock()

        from fastapi.testclient import TestClient
        client = TestClient(
            create_app(broker=mock_broker, search_repository=FailingRepository())
        )

        resp = client.post("/api/search", json={"query": "test", "mode": "discovery"})

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Search could not be durably accepted"

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
    async def test_search_endpoint_can_include_attribution(self):
        from argus.api.main import create_app
        from argus.models import ProviderName, SearchMode, SearchResponse, SearchResult

        mock_broker = MagicMock()
        mock_broker.search = AsyncMock(return_value=SearchResponse(
            query="test",
            mode=SearchMode.DISCOVERY,
            results=[
                SearchResult(
                    url="https://example.com",
                    title="Test",
                    snippet="A page",
                    provider=ProviderName.DUCKDUCKGO,
                    score=0.5,
                    score_attribution={"duckduckgo": 0.5},
                )
            ],
            total_results=1,
            search_run_id="attribution-1",
        ))
        mock_broker.cache = MagicMock()
        mock_broker.health_tracker = MagicMock()
        mock_broker.budget_tracker = MagicMock()

        from fastapi.testclient import TestClient
        client = TestClient(create_app(broker=mock_broker))

        resp = client.post(
            "/api/search",
            json={
                "query": "test",
                "mode": "discovery",
                "include_attribution": True,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["results"][0]["score_attribution"] == {"duckduckgo": 0.5}
        mock_broker.search.assert_awaited_once()
        assert mock_broker.search.await_args.kwargs["compute_attribution"] is True
        assert mock_broker.search.await_args.kwargs["persist_legacy"] is False

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
        assert "browser" in resp.json()["runtime"]


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
