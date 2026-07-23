import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def env_secret():
    return "test-secret"


@pytest.fixture
def client_with_secret():
    def _make(secret: str = "test-secret"):
        with patch.dict("os.environ", {
            "ARGUS_EGRESS_SHARED_SECRET": secret,
            "ARGUS_MACHINE_NAME": "test-worker",
        }):
            from argus.worker.server import create_worker_app
            app = create_worker_app()
            client = TestClient(app)
            # Keep the client alive while the patch is active
            yield client
    return _make


def test_health_endpoint_returns_ok(client_with_secret):
    with patch.dict("os.environ", {
        "ARGUS_EGRESS_SHARED_SECRET": "test-secret",
        "ARGUS_MACHINE_NAME": "test-worker",
    }):
        from argus.worker.server import create_worker_app
        app = create_worker_app()
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_exec_requires_auth():
    with patch.dict("os.environ", {
        "ARGUS_EGRESS_SHARED_SECRET": "real-secret",
        "ARGUS_MACHINE_NAME": "test-worker",
    }):
        from argus.worker.server import create_worker_app
        app = create_worker_app()
        client = TestClient(app)
        resp = client.post(
            "/exec",
            json={"provider": "duckduckgo", "query": "test", "max_results": 1, "mode": "discovery"},
            headers={"Authorization": "Bearer wrong-secret"},
        )
        assert resp.status_code == 401


def test_exec_unknown_provider_returns_400():
    with patch.dict("os.environ", {
        "ARGUS_EGRESS_SHARED_SECRET": "s",
        "ARGUS_MACHINE_NAME": "test-worker",
    }):
        from argus.worker.server import create_worker_app
        app = create_worker_app()
        client = TestClient(app)
        resp = client.post(
            "/exec",
            json={"provider": "nonexistent", "query": "test", "max_results": 1, "mode": "discovery"},
            headers={"Authorization": "Bearer s"},
        )
        assert resp.status_code == 400


def test_exec_returns_results_on_success():
    from argus.models import SearchResult, ProviderName, ProviderTrace

    fake_results = [
        SearchResult(url="https://example.com", title="Ex", snippet="Ex snip",
                     provider=ProviderName.DUCKDUCKGO)
    ]
    fake_trace = ProviderTrace(provider=ProviderName.DUCKDUCKGO, status="success",
                               results_count=1, latency_ms=50)

    async def fake_search(query):
        return fake_results, fake_trace

    with patch.dict("os.environ", {
        "ARGUS_EGRESS_SHARED_SECRET": "s",
        "ARGUS_MACHINE_NAME": "test-worker",
    }):
        with patch("argus.worker.server._get_provider") as mock_get:
            mock_provider = AsyncMock()
            mock_provider.search = fake_search
            mock_get.return_value = mock_provider

            from argus.worker.server import create_worker_app
            app = create_worker_app()
            client = TestClient(app)
            resp = client.post(
                "/exec",
                json={"provider": "duckduckgo", "query": "test", "max_results": 1, "mode": "discovery"},
                headers={"Authorization": "Bearer s"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["trace"]["status"] == "success"
            assert len(data["results"]) == 1
            assert data["results"][0]["url"] == "https://example.com"


@pytest.mark.parametrize("secret", ["s", ""])
def test_exec_fails_closed_for_paid_providers_without_calling_adapter(secret):
    with patch.dict(
        "os.environ",
        {
            "ARGUS_EGRESS_SHARED_SECRET": secret,
            "ARGUS_MACHINE_NAME": "test-worker",
        },
        clear=True,
    ):
        with patch("argus.worker.server._get_provider") as get_provider:
            from argus.worker.server import create_worker_app

            client = TestClient(create_worker_app())
            headers = {"Authorization": "Bearer s"} if secret else {}
            response = client.post(
                "/exec",
                json={
                    "provider": "brave",
                    "query": "must not spend",
                    "max_results": 1,
                    "mode": "discovery",
                },
                headers=headers,
            )

    assert response.status_code == 403
    assert "paid providers" in response.json()["detail"]
    get_provider.assert_not_called()
