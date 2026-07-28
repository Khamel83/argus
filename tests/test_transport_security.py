from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient


def _client(monkeypatch):
    from argus.api.main import create_app
    from argus.config import reset_config

    monkeypatch.setenv("ARGUS_ACCEPTED_OPERATION_AUTHORITY", "legacy")
    monkeypatch.setenv("ARGUS_ALLOWED_ORIGINS", "https://maya.example")
    reset_config()
    service = MagicMock()
    service.search = AsyncMock()
    return TestClient(create_app(accepted_operation_service=service)), service


def test_invalid_host_rejects_before_accepted_service(monkeypatch):
    client, service = _client(monkeypatch)

    response = client.post(
        "/api/v2/search",
        json={"query": "never executes"},
        headers={"host": "evil.example"},
    )

    assert response.status_code == 421
    assert response.json()["error"]["code"] == "misdirected_request"
    service.search.assert_not_called()


def test_invalid_origin_rejects_before_accepted_service(monkeypatch):
    client, service = _client(monkeypatch)

    response = client.post(
        "/api/v2/search",
        json={"query": "never executes"},
        headers={"origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "policy_rejected"
    service.search.assert_not_called()


def test_ambiguous_v2_credentials_reject_before_execution(monkeypatch):
    client, service = _client(monkeypatch)

    response = client.post(
        "/api/v2/search",
        json={"query": "never executes"},
        headers={
            "authorization": "Bearer one",
            "x-api-key": "two",
        },
    )

    assert response.status_code == 401
    assert response.json()["outcome"] == "authentication_rejected"
    assert response.headers["www-authenticate"] == "Bearer"
    service.search.assert_not_called()


def test_v2_rejects_unsupported_media_and_oversized_body_before_execution(
    monkeypatch,
):
    client, service = _client(monkeypatch)

    unsupported = client.post(
        "/api/v2/search",
        content="not json",
        headers={"content-type": "text/plain"},
    )
    oversized = client.post(
        "/api/v2/search",
        content=b"{}",
        headers={
            "content-type": "application/json",
            "content-length": str(1_048_577),
        },
    )

    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["code"] == "unsupported_media_type"
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "payload_too_large"
    service.search.assert_not_called()


def test_v2_counts_actual_body_when_content_length_is_false(monkeypatch):
    client, service = _client(monkeypatch)

    response = client.post(
        "/api/v2/search",
        content=b" " * 1_048_577,
        headers={
            "content-type": "application/json",
            "content-length": "2",
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    service.search.assert_not_called()


def test_remote_production_fails_startup_without_complete_security_policy(
    monkeypatch,
):
    import pytest

    from argus.api.main import create_app
    from argus.api.security import TransportSecurityConfigurationError
    from argus.config import reset_config

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_NODE_ROLE", "primary")
    monkeypatch.setenv("ARGUS_HOST", "0.0.0.0")
    monkeypatch.delenv("ARGUS_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("ARGUS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("ARGUS_API_KEY", raising=False)
    reset_config()
    with pytest.raises(TransportSecurityConfigurationError, match="allowed hosts"):
        create_app()

    monkeypatch.setenv("ARGUS_ALLOWED_HOSTS", "argus.internal")
    reset_config()
    with pytest.raises(TransportSecurityConfigurationError, match="Origin policy"):
        create_app()

    monkeypatch.setenv("ARGUS_ALLOWED_ORIGINS", "")
    reset_config()
    with pytest.raises(TransportSecurityConfigurationError, match="bearer"):
        create_app()

    monkeypatch.setenv("ARGUS_API_KEY", "caller-token")
    reset_config()
    try:
        create_app()
    finally:
        reset_config()


def test_v2_authentication_and_rate_limit_failures_use_the_v2_envelope(monkeypatch):
    from argus.api.main import create_app
    from argus.config import reset_config

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_NODE_ROLE", "primary")
    monkeypatch.setenv("ARGUS_API_KEY", "caller-token")
    reset_config()
    try:
        unauthenticated = TestClient(create_app()).post(
            "/api/v2/search",
            json={"query": "blocked"},
        )
    finally:
        reset_config()

    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["outcome"] == "authentication_rejected"
    assert unauthenticated.headers["www-authenticate"] == "Bearer"

    limiter = MagicMock()
    limiter.is_allowed.return_value = (False, {"Retry-After": "7"})
    client = TestClient(create_app(rate_limiter=limiter))
    limited = client.post("/api/v2/search", json={"query": "blocked"})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.json()["error"]["retry_after_seconds"] == 7
    assert limited.headers["retry-after"] == "7"
