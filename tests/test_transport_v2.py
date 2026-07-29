from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from argus.contracts import AcceptedOperation, CanonicalOutcome, OperationError


def _error(outcome: CanonicalOutcome, request_id: str) -> OperationError:
    code = outcome.value
    status = {
        CanonicalOutcome.INVALID_REQUEST: 422,
        CanonicalOutcome.AUTHENTICATION_REJECTED: 401,
        CanonicalOutcome.POLICY_REJECTED: 403,
        CanonicalOutcome.TIMEOUT: 504,
        CanonicalOutcome.PERSISTENCE_FAILED: 503,
        CanonicalOutcome.PROVIDERS_FAILED: 502,
        CanonicalOutcome.EXTRACTION_FAILED: 502,
        CanonicalOutcome.UNREADY: 503,
    }[outcome]
    return OperationError(
        outcome=outcome,
        type=f"urn:argus:problem:{code}",
        title=code.replace("_", " ").title(),
        status=status,
        detail="Safe bounded failure",
        instance=f"urn:argus:request:{request_id}",
        code=code,
        retryable=False,
        retry_after_seconds=None,
    )


@pytest.mark.parametrize("outcome", tuple(CanonicalOutcome))
def test_v2_presenter_emits_exact_envelope_status_and_request_headers(outcome):
    from argus.api.contracts_v2 import EvidenceHttpPresenter

    request_id = f"request-{outcome.value}"
    success_like = outcome in {
        CanonicalOutcome.SUCCESS,
        CanonicalOutcome.DEGRADED,
        CanonicalOutcome.EMPTY,
    }
    operation = AcceptedOperation(
        outcome=outcome,
        request_id=request_id,
        result={"accepted": True} if success_like else None,
        error=None if success_like else _error(outcome, request_id),
    )

    response = EvidenceHttpPresenter().response(operation)

    assert response.status_code == (200 if success_like else operation.error.status)
    assert response.headers["argus-contract-version"] == "2.0"
    assert response.headers["x-request-id"] == request_id
    body = __import__("json").loads(response.body)
    assert body == {
        "contract_version": "2.0",
        "outcome": outcome.value,
        "request_id": request_id,
        "result": {"accepted": True} if success_like else None,
        "error": None
        if success_like
        else {
            "type": f"urn:argus:problem:{outcome.value}",
            "title": outcome.value.replace("_", " ").title(),
            "status": operation.error.status,
            "detail": "Safe bounded failure",
            "instance": f"urn:argus:request:{request_id}",
            "code": outcome.value,
            "retryable": False,
            "retry_after_seconds": None,
        },
    }
    if outcome is CanonicalOutcome.AUTHENTICATION_REJECTED:
        assert response.headers["www-authenticate"] == "Bearer"


def test_v2_is_unready_without_atomic_evidence_authority(monkeypatch):
    from argus.api.main import create_app
    from argus.config import reset_config

    monkeypatch.setenv("ARGUS_ACCEPTED_OPERATION_AUTHORITY", "legacy")
    reset_config()
    service = MagicMock()
    service.search = AsyncMock()
    try:
        client = TestClient(create_app(accepted_operation_service=service))
        response = client.post("/api/v2/search", json={"query": "disabled"})
    finally:
        reset_config()

    assert response.status_code == 503
    assert response.json()["outcome"] == "unready"
    assert response.json()["error"]["detail"] == "Evidence authority is not enabled"
    service.search.assert_not_called()


def test_v2_search_calls_the_same_accepted_service_once_when_registered(monkeypatch):
    from argus.api.main import create_app
    from argus.config import reset_config
    from argus.operations.accepted import (
        AcceptedOperationRegistration,
        AcceptedOperationService,
    )

    monkeypatch.setenv("ARGUS_ACCEPTED_OPERATION_AUTHORITY", "evidence")
    reset_config()
    operation = AcceptedOperation(
        outcome=CanonicalOutcome.EMPTY,
        request_id="request-v2",
        result={"accepted": True},
        error=None,
    )
    service = AcceptedOperationService(
        broker_provider=MagicMock(),
        repository_provider=MagicMock(),
        session_authority=MagicMock(),
        registration=AcceptedOperationRegistration.complete(),
    )
    service._evidence_repository = MagicMock()
    service.search = AsyncMock(return_value=operation)
    try:
        client = TestClient(create_app(accepted_operation_service=service))
        response = client.post(
            "/api/v2/search",
            json={"query": "v2"},
            headers={"x-request-id": "request-v2"},
        )
    finally:
        reset_config()

    assert response.status_code == 200
    assert response.json()["outcome"] == "empty"
    service.search.assert_awaited_once()


def test_v2_framework_errors_are_enveloped_without_rejected_value_echo(monkeypatch):
    from argus.api.main import create_app
    from argus.config import reset_config

    monkeypatch.setenv("ARGUS_ACCEPTED_OPERATION_AUTHORITY", "legacy")
    reset_config()
    try:
        client = TestClient(create_app(accepted_operation_service=MagicMock()))
        malformed = client.post(
            "/api/v2/search",
            content=b"{",
            headers={"content-type": "application/json"},
        )
        semantic = client.post(
            "/api/v2/search",
            json={"query": "secret-query", "mode": "not-a-mode"},
        )
        missing = client.get("/api/v2/not-a-route")
        wrong_method = client.get("/api/v2/search")
    finally:
        reset_config()

    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "malformed_request"
    assert semantic.status_code == 422
    assert semantic.json()["error"]["code"] == "invalid_request"
    assert "secret-query" not in semantic.text
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "route_not_found"
    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "POST"
    assert wrong_method.json()["error"]["code"] == "method_not_allowed"


def test_capabilities_advertise_v2_only_after_complete_registration(monkeypatch):
    from argus.api.main import create_app
    from argus.config import reset_config
    from argus.operations.accepted import (
        AcceptedOperationRegistration,
        AcceptedOperationService,
    )

    monkeypatch.setenv("ARGUS_ACCEPTED_OPERATION_AUTHORITY", "legacy")
    reset_config()
    legacy = TestClient(create_app()).get("/api/capabilities").json()
    assert "http_contracts" not in legacy

    monkeypatch.setenv("ARGUS_ACCEPTED_OPERATION_AUTHORITY", "evidence")
    reset_config()
    service = AcceptedOperationService(
        broker_provider=MagicMock(),
        repository_provider=MagicMock(),
        session_authority=MagicMock(),
        registration=AcceptedOperationRegistration.complete(),
    )
    service._evidence_repository = MagicMock()
    try:
        evidence = TestClient(
            create_app(accepted_operation_service=service)
        ).get("/api/capabilities").json()
    finally:
        reset_config()

    assert evidence["http_contracts"] == [
        {"version": "1", "base_path": "/api", "legacy": True},
        {"version": "2.0", "base_path": "/api/v2", "legacy": False},
    ]
    assert evidence["mcp_contract"] == {
        "transport": "streamable-http",
        "endpoint": "/mcp",
        "argus_tool_contract_versions": ["1", "2.0"],
        "version_2_tool_suffix": "_v2",
    }


def test_evidence_capability_fails_startup_without_session_authority(monkeypatch):
    from argus.api.main import create_app
    from argus.config import reset_config
    from argus.operations.accepted import (
        AcceptedAuthorityConfigurationError,
        AcceptedOperationRegistration,
        AcceptedOperationService,
    )

    monkeypatch.setenv("ARGUS_ACCEPTED_OPERATION_AUTHORITY", "evidence")
    monkeypatch.delenv("ARGUS_RETRIEVAL_SESSION_SECRET", raising=False)
    reset_config()
    service = AcceptedOperationService(
        broker_provider=MagicMock(),
        repository_provider=MagicMock(),
        registration=AcceptedOperationRegistration.complete(),
    )
    service._evidence_repository = MagicMock()
    try:
        with pytest.raises(
            AcceptedAuthorityConfigurationError,
            match="RETRIEVAL_SESSION_SECRET",
        ):
            create_app(accepted_operation_service=service)
    finally:
        reset_config()


def test_release_capability_manifest_is_immutable_and_fail_closed():
    from argus.capabilities import CapabilityManifestError, http_capability_manifest

    registrations = {
        "accepted_service",
        "legacy_presenter",
        "v2_presenter",
        "v2_routes",
        "transport_security",
    }
    manifest = http_capability_manifest(
        evidence_enabled=True,
        registrations=registrations,
    )
    assert manifest.snapshot["http_contracts"][1]["version"] == "2.0"
    with pytest.raises(TypeError):
        manifest.snapshot["http_contracts"] = ()
    with pytest.raises(CapabilityManifestError, match="presenter"):
        http_capability_manifest(
            evidence_enabled=True,
            registrations={"accepted_service", "v2_routes"},
        )
