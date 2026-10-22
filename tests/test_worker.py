import pytest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def explicit_development(monkeypatch):
    monkeypatch.setenv("ARGUS_ENV", "development")


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


@pytest.mark.parametrize("secret", ["s"])
def test_exec_fails_closed_for_paid_providers_without_calling_adapter(secret):
    with patch.dict(
        "os.environ",
        {
            "ARGUS_EGRESS_SHARED_SECRET": secret,
            "ARGUS_MACHINE_NAME": "test-worker",
            "ARGUS_ENV": "development",
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


@pytest.mark.parametrize("secret", [None, "", "   "])
def test_missing_secret_rejected_at_startup_and_by_auth(monkeypatch, secret):
    from argus.worker.server import _check_auth, create_worker_app

    if secret is None:
        monkeypatch.delenv("ARGUS_EGRESS_SHARED_SECRET", raising=False)
    else:
        monkeypatch.setenv("ARGUS_EGRESS_SHARED_SECRET", secret)
    with pytest.raises(RuntimeError, match="requires ARGUS_EGRESS_SHARED_SECRET"):
        create_worker_app()
    request = type("Request", (), {"headers": {}})()
    with pytest.raises(HTTPException) as error:
        _check_auth(request)
    assert error.value.status_code == 401


@pytest.mark.parametrize("environment", [None, "", "production", "staging", "developmnt"])
def test_worker_requires_explicit_development(monkeypatch, environment):
    from argus.worker.server import create_worker_app

    monkeypatch.setenv("ARGUS_EGRESS_SHARED_SECRET", "s")
    if environment is None:
        monkeypatch.delenv("ARGUS_ENV", raising=False)
    else:
        monkeypatch.setenv("ARGUS_ENV", environment)
    with pytest.raises(RuntimeError, match="ARGUS_ENV=development"):
        create_worker_app()


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
                                    {"Authorization": "Basic s"}])
def test_unauthenticated_exec_never_constructs_provider(monkeypatch, headers):
    from argus.worker.server import create_worker_app

    monkeypatch.setenv("ARGUS_EGRESS_SHARED_SECRET", "s")
    with patch("argus.worker.server._get_provider") as get_provider:
        with TestClient(create_worker_app()) as client:
            response = client.post("/exec", json={"provider": "duckduckgo", "query": "test"},
                                   headers=headers)
    assert response.status_code == 401
    get_provider.assert_not_called()


def test_secret_removed_after_startup_never_dispatches(monkeypatch):
    from argus.worker.server import create_worker_app

    monkeypatch.setenv("ARGUS_EGRESS_SHARED_SECRET", "s")
    app = create_worker_app()
    monkeypatch.delenv("ARGUS_EGRESS_SHARED_SECRET")
    with patch("argus.worker.server._get_provider") as get_provider:
        with TestClient(app) as client:
            response = client.post("/exec", json={"provider": "duckduckgo", "query": "test"},
                                   headers={"Authorization": "Bearer s"})
    assert response.status_code == 401
    get_provider.assert_not_called()


def test_worker_cli_defaults_to_loopback(monkeypatch):
    from click.testing import CliRunner
    from argus.cli.main import cli

    monkeypatch.setenv("ARGUS_EGRESS_SHARED_SECRET", "s")
    monkeypatch.delenv("ARGUS_WORKER_BIND", raising=False)
    with patch("uvicorn.run") as run:
        result = CliRunner().invoke(cli, ["worker"])
    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 8273}
