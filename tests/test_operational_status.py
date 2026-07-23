"""Truthful, cached operational status behavior."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


def _service(*, production=True):
    from argus.operations.status import OperationalStatusService

    return OperationalStatusService(
        production=production,
        build={
            "version": "1.6.2",
            "source_revision": "a" * 40,
            "lock_sha256": "b" * 64,
        },
        deployment={"deployment_id": "deploy-7", "environment": "production"},
        authority={
            "role": "primary",
            "backend": "postgresql",
            "machine": "argus-1",
        },
        capabilities={"search": True, "browser": True},
        clock=lambda: NOW,
    )


def _healthy_required(service):
    service.mark_initialized(source="startup")
    for name in ("postgresql", "schema", "outbox"):
        service.observe_dependency(
            name,
            state="healthy",
            source="authority",
            ttl=timedelta(minutes=5),
        )
    service.observe_provider(
        "duckduckgo",
        "capability",
        state="healthy",
        source="runtime_config",
        ttl=timedelta(minutes=5),
    )
    service.observe_provider(
        "duckduckgo",
        "reachability",
        state="healthy",
        source="probe",
        ttl=timedelta(minutes=5),
    )


def test_observation_expiry_is_unknown_and_state_changes_move_last_transition():
    from argus.operations.status import ObservationStore

    store = ObservationStore(clock=lambda: NOW)
    first = store.observe(
        "postgresql",
        state="healthy",
        source="authority_probe",
        observed_at=NOW,
        ttl=timedelta(minutes=1),
        reason="connected",
    )
    same = store.observe(
        "postgresql",
        state="healthy",
        source="authority_probe",
        observed_at=NOW + timedelta(seconds=20),
        ttl=timedelta(minutes=1),
    )
    changed = store.observe(
        "postgresql",
        state="unready",
        source="authority_probe",
        observed_at=NOW + timedelta(seconds=30),
        ttl=timedelta(minutes=1),
        reason="password=do-not-leak https://database.internal",
    )

    assert same.last_transition == first.last_transition == NOW
    assert changed.last_transition == NOW + timedelta(seconds=30)
    rendered = changed.as_dict(now=NOW + timedelta(minutes=2))
    assert rendered["state"] == "unknown"
    assert rendered["stale"] is True
    assert rendered["reason"] == "observation_expired"
    assert "do-not-leak" not in str(changed.as_dict(now=NOW))
    assert "database.internal" not in str(changed.as_dict(now=NOW))


@pytest.mark.parametrize(
    ("dependency", "state", "expected"),
    [
        ("postgresql", "unready", "unready"),
        ("schema", "unknown", "unready"),
        ("outbox", "unready", "unready"),
        ("maya", "unready", "degraded"),
        ("browser", "unready", "degraded"),
        ("recovery", "unknown", "degraded"),
    ],
)
def test_required_and_optional_dependency_classification(
    dependency,
    state,
    expected,
):
    service = _service()
    _healthy_required(service)
    service.observe_dependency(
        dependency,
        state=state,
        source="test",
        ttl=timedelta(minutes=5),
    )

    assert service.full_status()["status"] == expected


def test_provider_partial_loss_degrades_but_total_loss_is_unready_and_recovers():
    service = _service()
    _healthy_required(service)
    service.observe_provider(
        "brave",
        "capability",
        state="healthy",
        source="runtime_config",
        ttl=timedelta(minutes=5),
    )
    service.observe_provider(
        "brave",
        "reachability",
        state="unready",
        source="probe",
        ttl=timedelta(minutes=5),
        reason="probe_failed",
    )

    assert service.full_status()["status"] == "degraded"

    service.observe_provider(
        "duckduckgo",
        "reachability",
        state="unready",
        source="probe",
        ttl=timedelta(minutes=5),
    )
    assert service.full_status()["status"] == "unready"

    service.observe_provider(
        "duckduckgo",
        "reachability",
        state="healthy",
        source="probe",
        ttl=timedelta(minutes=5),
    )
    assert service.full_status()["status"] == "degraded"


def test_service_instance_identity_is_unique_per_process_service():
    first = _service()
    second = _service()

    assert first.full_status()["identity"]["service_instance_id"]
    assert (
        first.full_status()["identity"]["service_instance_id"]
        != second.full_status()["identity"]["service_instance_id"]
    )
    assert first.full_status()["build"]["source_revision"] == "a" * 40


def test_metrics_reject_high_cardinality_labels_and_remain_bounded():
    from argus.operations.status import BoundedMetrics

    metrics = BoundedMetrics()
    metrics.record_request(
        route="/api/search",
        method="POST",
        status_code=200,
        latency_seconds=0.2,
    )
    metrics.record_request(
        route="/api/search/secret-user-path",
        method="POST",
        status_code=500,
        latency_seconds=0.4,
    )

    snapshot = metrics.snapshot()
    assert snapshot["requests"][0]["labels"] == {
        "route": "/api/search",
        "method": "POST",
        "status_class": "2xx",
        "outcome": "success",
    }
    assert snapshot["requests"][1]["labels"]["route"] == "unmatched"
    serialized = str(snapshot)
    assert "secret-user-path" not in serialized
    assert len(snapshot["requests"]) <= metrics.max_series

    with pytest.raises(ValueError, match="label"):
        metrics.increment(
            "requests",
            {"request_id": "caller-controlled", "route": "/api/search"},
        )


def test_request_correlation_accepts_only_bounded_safe_values():
    from argus.operations.status import safe_correlation_id

    assert safe_correlation_id("request-123") == "request-123"
    generated = safe_correlation_id("query=https://secret.example/" + "x" * 200)
    assert len(generated) == 16
    assert generated.isalnum()


def test_public_status_is_minimal_and_admin_status_is_authenticated(monkeypatch):
    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.config import reset_config

    monkeypatch.setenv("ARGUS_ADMIN_API_KEY", "admin-secret")
    reset_config()
    service = _service()
    _healthy_required(service)
    service.observe_dependency(
        "browser",
        state="degraded",
        source="runtime_manifest",
        ttl=timedelta(minutes=5),
        reason="browser_artifact_unavailable",
    )
    client = TestClient(
        create_app(
            broker_factory=MagicMock(side_effect=AssertionError("no broker call")),
            operational_status=service,
        )
    )

    live = client.get("/api/live")
    startup = client.get("/api/startup")
    ready = client.get("/api/ready")
    denied = client.get("/api/admin/status")
    detailed = client.get(
        "/api/admin/status",
        headers={"X-Admin-API-Key": "admin-secret"},
    )

    assert live.status_code == 200
    assert live.json() == {"status": "alive"}
    assert startup.status_code == 200
    assert set(startup.json()) == {"status", "initialized", "version"}
    assert ready.status_code == 200
    assert ready.json()["status"] == "degraded"
    assert "dependencies" not in ready.json()
    assert denied.status_code == 401
    assert detailed.status_code == 200
    assert detailed.json()["dependencies"]["browser"]["state"] == "degraded"
    assert "source_revision" in detailed.json()["build"]


def test_cached_readiness_returns_503_without_calling_dependencies():
    from fastapi.testclient import TestClient

    from argus.api.main import create_app

    service = _service()
    service.mark_initialized(source="startup")
    client = TestClient(
        create_app(
            broker_factory=MagicMock(side_effect=AssertionError("no broker call")),
            operational_status=service,
        )
    )

    response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unready"


def test_invalid_request_id_is_replaced_and_never_used_as_a_metric_label():
    from fastapi.testclient import TestClient

    from argus.api.main import create_app

    service = _service()
    client = TestClient(create_app(broker=MagicMock(), operational_status=service))
    secret = "query=https://secret.example/private?token=must-not-leak"

    response = client.get("/api/live", headers={"X-Request-ID": secret})

    assert response.status_code == 200
    assert response.headers["x-request-id"] != secret
    assert "must-not-leak" not in str(service.metrics.snapshot())


def test_dependency_refresh_maps_repository_provider_browser_and_recovery_truth():
    from argus.operations.status import refresh_operational_status

    service = _service()
    repository = MagicMock()
    repository.operational_status.return_value = {
        "backend": "postgresql",
        "connected": True,
        "schema_head": "0006_maya_outbox",
        "outbox": {
            "counts": {"pending": 2, "dead_letter": 1},
            "oldest_pending_age_seconds": 12,
            "dead_letter_oldest_age_seconds": 20,
        },
    }
    broker = MagicMock()
    broker.get_provider_status.side_effect = lambda provider: {
        "provider": provider.value,
        "config_status": (
            "enabled" if provider.value in {"duckduckgo", "brave"} else "disabled_by_config"
        ),
        "effective_status": (
            "enabled"
            if provider.value == "duckduckgo"
            else "temporarily_disabled_after_failures"
            if provider.value == "brave"
            else "disabled_by_config"
        ),
        "health": (
            {
                "consecutive_failures": 5,
                "last_success": NOW.timestamp() - 30,
                "last_failure": NOW.timestamp() - 5,
                "disabled_until": NOW.timestamp() + 120,
            }
            if provider.value == "brave"
            else None
        ),
        "budget_remaining": None if provider.value == "duckduckgo" else 0,
    }
    broker._reachability.get_all.return_value = {}
    broker.budget_tracker.get_budget_limit.side_effect = (
        lambda provider: 0 if provider.value == "duckduckgo" else 100
    )
    broker.spend_repository.provider_summary.side_effect = (
        lambda provider, budget_limit: {
            "remaining": None if budget_limit == 0 else 0,
            "argus_estimated_charge": 0,
            "uncertain_charge": 0,
            "provider_snapshot": None,
        }
    )

    refresh_operational_status(
        service,
        broker=broker,
        repository=repository,
        browser_status={
            "declared": True,
            "available": False,
            "loaded": False,
            "degraded_reason": "browser_artifact_unavailable",
        },
        recovery_status={
            "state": "degraded",
            "schema_promotion_allowed": False,
            "reasons": ["backup_stale"],
        },
        now=NOW,
    )

    status = service.full_status()
    assert status["status"] == "degraded"
    assert status["dependencies"]["postgresql"]["state"] == "healthy"
    assert status["dependencies"]["schema"]["details"]["schema_head"] == (
        "0006_maya_outbox"
    )
    assert status["dependencies"]["outbox"]["state"] == "healthy"
    assert status["dependencies"]["browser"]["state"] == "degraded"
    assert status["dependencies"]["recovery"]["state"] == "degraded"
    assert status["providers"]["duckduckgo"]["observations"]["balance"]["details"] == {
        "remaining": None,
        "unlimited": True,
    }
    brave = status["providers"]["brave"]["observations"]
    assert brave["cooldown"]["state"] == "unready"
    assert brave["health"]["source"] == "process_memory"
    assert status["promotion_allowed"] is False
    gauges = status["metrics"]["gauges"]
    assert gauges["outbox_pending"]["value"] == 2
    assert gauges["outbox_dead_letters"]["value"] == 1


def test_repository_authority_loss_is_cached_unready_and_restoration_recovers():
    from argus.operations.status import refresh_operational_status

    service = _service()
    repository = MagicMock()
    repository.operational_status.side_effect = RuntimeError(
        "postgresql://user:password@private/argus"
    )
    broker = MagicMock()

    refresh_operational_status(
        service,
        broker=broker,
        repository=repository,
        browser_status={"declared": False, "available": False, "loaded": False},
        recovery_status={"state": "unavailable", "reasons": ["not_configured"]},
        now=NOW,
    )

    failed = service.full_status()
    assert failed["status"] == "unready"
    assert failed["dependencies"]["postgresql"]["reason"] == "authority_unavailable"
    assert "password" not in str(failed)

    repository.operational_status.side_effect = None
    repository.operational_status.return_value = {
        "backend": "postgresql",
        "connected": True,
        "schema_head": "0006_maya_outbox",
        "outbox": {"counts": {}},
    }
    broker.get_provider_status.side_effect = lambda provider: {
        "provider": provider.value,
        "config_status": (
            "enabled" if provider.value == "duckduckgo" else "disabled_by_config"
        ),
        "effective_status": (
            "enabled" if provider.value == "duckduckgo" else "disabled_by_config"
        ),
        "health": None,
        "budget_remaining": None,
    }
    broker._reachability.get_all.return_value = {}
    broker.budget_tracker.get_budget_limit.return_value = 0
    broker.spend_repository.provider_summary.return_value = {
        "remaining": None,
        "argus_estimated_charge": 0,
        "uncertain_charge": 0,
        "provider_snapshot": None,
    }

    refresh_operational_status(
        service,
        broker=broker,
        repository=repository,
        browser_status={"declared": False, "available": False, "loaded": False},
        recovery_status={"state": "unavailable", "reasons": ["not_configured"]},
        now=NOW + timedelta(seconds=15),
    )

    recovered = service.full_status()
    assert recovered["ready"] is True
    assert recovered["status"] == "degraded"
    assert recovered["dependencies"]["postgresql"]["last_transition"] == (
        "2026-07-23T12:00:15Z"
    )


def test_operational_metrics_inventory_covers_required_bounded_gauges():
    service = _service()

    gauges = service.full_status()["metrics"]["gauges"]

    assert set(gauges) == {
        "outbox_pending",
        "outbox_dead_letters",
        "browser_memory_bytes",
        "browser_processes",
        "process_restarts",
        "accounting_uncertain_charge",
    }
    assert all(entry["state"] == "unknown" for entry in gauges.values())


def test_container_health_contract_targets_network_free_liveness():
    from pathlib import Path

    import yaml

    root = Path(__file__).parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load(
        (root / "docker-compose.yml").read_text(encoding="utf-8")
    )

    assert "/api/live" in dockerfile
    assert "/api/health" not in dockerfile
    command = " ".join(compose["services"]["argus"]["healthcheck"]["test"])
    assert "/api/live" in command
    assert "/api/health" not in command


def test_touched_request_paths_do_not_log_raw_query_or_exception_text():
    from pathlib import Path

    root = Path(__file__).parents[1]
    broker = (root / "argus" / "broker" / "router.py").read_text()
    session_flow = (root / "argus" / "broker" / "session_flow.py").read_text()
    execution = (root / "argus" / "broker" / "execution.py").read_text()
    usage = (root / "argus" / "api" / "usage.py").read_text()

    assert "Search complete: query=" not in broker
    assert "Cache hit for query:" not in broker
    assert "Query refined: %r -> %r" not in session_flow
    assert "provider_name, exc" not in execution
    assert 'logger.warning("usage: %s failed: %s", method, exc)' not in usage


def test_sqlite_repository_reports_bounded_authority_status(tmp_path):
    from argus.persistence.search_ledger import create_search_ledger_repository

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'status.db'}"
    )

    status = repository.operational_status(now=NOW)

    assert status == {
        "backend": "sqlite",
        "connected": True,
        "schema_head": "sqlite-managed",
        "outbox": {
            "counts": {},
            "oldest_pending_age_seconds": None,
            "dead_letter_oldest_age_seconds": None,
            "dead_letter_payload_bytes": 0,
        },
    }
    assert "url" not in str(status).lower()
    assert "password" not in str(status).lower()


def test_startup_dependency_failure_does_not_take_down_liveness():
    from fastapi.testclient import TestClient

    from argus.api.main import create_app

    service = _service()
    app = create_app(
        broker_factory=MagicMock(side_effect=RuntimeError("database unavailable")),
        operational_status=service,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        live = client.get("/api/live")
        startup = client.get("/api/startup")
        ready = client.get("/api/ready")

    assert live.status_code == 200
    assert startup.status_code == 200
    assert startup.json()["status"] == "failed"
    assert ready.status_code == 503


def test_request_log_uses_safe_correlation_and_route_template(monkeypatch):
    from fastapi.testclient import TestClient

    from argus.api.main import create_app

    service = _service()
    logged = MagicMock()
    monkeypatch.setattr("argus.api.main.logger.info", logged)
    client = TestClient(create_app(broker=MagicMock(), operational_status=service))
    unsafe = "query=https://secret.example/private?token=must-not-leak"

    response = client.get("/api/live", headers={"X-Request-ID": unsafe})

    assert response.status_code == 200
    messages = "\n".join(
        str(call.args[0]) % call.args[1:] for call in logged.call_args_list
    )
    assert "http_request" in messages
    assert "route=/api/live" in messages
    assert f"request_id={response.headers['x-request-id']}" in messages
    assert "must-not-leak" not in messages
    assert "secret.example" not in messages


def test_provider_health_compatibility_surface_includes_cached_nested_evidence():
    from fastapi.testclient import TestClient

    from argus.api.main import create_app

    service = _service()
    _healthy_required(service)
    service.observe_provider(
        "brave",
        "capability",
        state="healthy",
        source="runtime_config",
        ttl=timedelta(minutes=5),
    )
    service.observe_provider(
        "brave",
        "reachability",
        state="unready",
        source="reachability_probe",
        ttl=timedelta(minutes=5),
        reason="probe_failed",
    )
    broker = MagicMock()
    broker.get_provider_status.side_effect = lambda provider: {
        "provider": provider.value,
        "effective_status": "enabled",
    }
    client = TestClient(
        create_app(broker=broker, operational_status=service)
    )

    response = client.get("/api/provider-health")

    assert response.status_code == 200
    brave = response.json()["providers"]["brave"]
    assert brave["state"] == "unready"
    assert brave["observations"]["reachability"]["reason"] == "probe_failed"
