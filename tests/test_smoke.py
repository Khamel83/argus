"""Smoke tests — verify the app starts and basic endpoints respond."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from argus.api.main import app
    with TestClient(app) as c:
        yield c


class TestSmokeStartup:
    def test_health_returns_ok_or_degraded(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("ok", "degraded")

    def test_health_detail_returns_provider_list(self, client):
        resp = client.get("/api/health/detail")
        assert resp.status_code == 200
        body = resp.json()
        assert "providers" in body
        assert "health_tracking" in body

    def test_search_returns_expected_schema(self, client):
        resp = client.post(
            "/api/search",
            json={"query": "test", "mode": "discovery"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "query" in body
        assert "results" in body
        assert "mode" in body
        assert isinstance(body["results"], list)

    def test_search_without_api_key_when_none_set(self, client):
        resp = client.post(
            "/api/search",
            json={"query": "test", "mode": "discovery"},
        )
        assert resp.status_code == 200

    def test_extract_returns_error_for_bogus_url(self, client):
        resp = client.post(
            "/api/extract",
            json={"url": "http://127.0.0.1:1/this-does-not-exist"},
        )
        # Should not 500 — either 200 with error field or 422
        assert resp.status_code in (200, 422)
        if resp.status_code == 200:
            body = resp.json()
            # Jina or trafilatura may succeed with empty content
            assert "url" in body or "detail" in body

    def test_validation_error_has_standard_shape(self, client):
        resp = client.post("/api/search", json={"query": "test", "mode": "invalid_mode"})
        assert resp.status_code == 422
        body = resp.json()
        assert "error" in body
        assert "detail" in body
        assert isinstance(body["detail"], list)

    def test_nonexistent_route_has_standard_shape(self, client):
        resp = client.get("/api/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body
