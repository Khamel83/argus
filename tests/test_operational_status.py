"""Truthful, cached operational status behavior."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

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
    for dimension in ("health", "cooldown", "balance"):
        service.observe_provider(
            "duckduckgo",
            dimension,
            state="healthy",
            source="test_evidence",
            ttl=timedelta(minutes=5),
        )


def _runtime_broker():
    broker = MagicMock()
    broker.get_provider_status.side_effect = lambda provider: {
        "provider": provider.value,
        "config_status": (
            "enabled" if provider.value == "duckduckgo" else "disabled_by_config"
        ),
        "effective_status": (
            "enabled" if provider.value == "duckduckgo" else "disabled_by_config"
        ),
        "health": (
            {
                "consecutive_failures": 0,
                "last_success": NOW.timestamp(),
                "last_failure": None,
                "disabled_until": None,
            }
            if provider.value == "duckduckgo"
            else None
        ),
        "budget_remaining": None,
    }
    broker._reachability.get_all.return_value = {
        "duckduckgo": {
            "probes": {
                "local": {
                    "reachable": True,
                    "last_checked": NOW.timestamp(),
                }
            }
        }
    }
    broker._reachability.probe_all = AsyncMock()
    broker._providers = {}
    broker.budget_tracker.get_budget_limit.return_value = 0
    broker.budget_tracker.close = MagicMock()
    broker.spend_repository.provider_summary.return_value = {
        "remaining": None,
        "argus_estimated_charge": 0,
        "uncertain_charge": 0,
        "provider_snapshot": None,
    }
    _configure_public_evidence(broker)
    return broker


def _configure_public_evidence(broker):
    from argus.models import ProviderName

    broker.operational_provider_evidence.side_effect = lambda: {
        provider.value: {
            "status": broker.get_provider_status(provider),
            "reachability": broker._reachability.get_all().get(provider.value)
            or broker._reachability.get_all().get(provider),
        }
        for provider in ProviderName
    }


def _runtime_repository():
    from argus.api.lifecycle import LifecycleCapability

    repository = MagicMock()
    repository.lifecycle_capability = LifecycleCapability.COOPERATIVE_BOUNDED
    repository.operational_status_capability = LifecycleCapability.COOPERATIVE_BOUNDED
    repository.compaction_capability = LifecycleCapability.COOPERATIVE_BOUNDED
    repository.operational_status.return_value = {
        "backend": "postgresql",
        "connected": True,
        "schema_head": "0006_maya_outbox",
        "outbox": {"counts": {}},
    }
    return repository


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


def test_observation_refresh_after_expiry_starts_a_new_transition():
    from argus.operations.status import ObservationStore

    store = ObservationStore(clock=lambda: NOW)
    store.observe(
        "browser",
        state="healthy",
        source="process_memory",
        observed_at=NOW,
        ttl=timedelta(seconds=10),
    )

    refreshed = store.observe(
        "browser",
        state="healthy",
        source="process_memory",
        observed_at=NOW + timedelta(seconds=20),
        ttl=timedelta(seconds=10),
    )

    assert refreshed.last_transition == NOW + timedelta(seconds=20)


def test_expired_observation_transitions_to_unknown_at_expiry():
    from argus.operations.status import ObservationStore

    store = ObservationStore(clock=lambda: NOW)
    store.observe(
        "backup",
        state="healthy",
        source="recovery_evidence",
        ttl=timedelta(seconds=30),
    )

    rendered = store.rendered(now=NOW + timedelta(seconds=31))["backup"]

    assert rendered["state"] == "unknown"
    assert rendered["last_transition"] == "2026-07-23T12:00:30Z"


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


def test_missing_optional_evidence_is_explicitly_degraded():
    service = _service()
    _healthy_required(service)

    status = service.full_status()

    assert status["status"] == "degraded"
    assert {"maya", "browser", "recovery"} <= set(status["reason_codes"])
    for dependency in ("maya", "browser", "recovery"):
        assert status["dependencies"][dependency]["state"] == "unknown"


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


@pytest.mark.parametrize("health_state", ["unknown", "degraded"])
def test_unproven_provider_health_does_not_count_as_a_usable_path(health_state):
    service = _service()
    _healthy_required(service)
    service.observe_provider(
        "duckduckgo",
        "health",
        state=health_state,
        source="process_memory",
        ttl=timedelta(minutes=5),
        reason="not_observed_since_restart",
    )

    status = service.full_status()

    assert status["status"] == "unready"
    assert status["reason_codes"] == ["retrieval_path"]


def test_fresh_health_tracker_does_not_invent_zeroed_provider_evidence():
    from argus.broker.health import HealthTracker
    from argus.broker.router import SearchBroker
    from argus.models import ProviderName, ProviderStatus

    provider = MagicMock()
    provider.status.return_value = ProviderStatus.ENABLED
    broker = SearchBroker.__new__(SearchBroker)
    broker._providers = {ProviderName.DUCKDUCKGO: provider}
    broker._health = HealthTracker()
    broker._budgets = MagicMock()
    broker._budgets.check_status.return_value = None
    broker._budgets.get_remaining_budget.return_value = None

    status = broker.get_provider_status(ProviderName.DUCKDUCKGO)

    assert status["health"] is None
    assert broker._health.get_all_status() == {}


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
    metrics.register_route_templates(["/api/search"])
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
    service.observe_dependency(
        "backup",
        state="healthy",
        source="recovery_evidence",
        ttl=timedelta(minutes=5),
        details={"evidence_at": "2026-07-23T05:00:00-07:00", "fresh": True},
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
    assert "evidence_at" not in str(ready.json())
    assert denied.status_code == 401
    assert detailed.status_code == 200
    assert detailed.json()["dependencies"]["browser"]["state"] == "degraded"
    assert detailed.json()["dependencies"]["backup"]["details"]["evidence_at"] == (
        "2026-07-23T12:00:00Z"
    )
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

    response = client.get("/api/ready", headers={"X-Request-ID": secret})

    assert response.status_code == 503
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
            "enabled"
            if provider.value in {"duckduckgo", "brave"}
            else "disabled_by_config"
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
            else {
                "consecutive_failures": 0,
                "last_success": NOW.timestamp() - 10,
                "last_failure": None,
                "disabled_until": None,
            }
            if provider.value == "duckduckgo"
            else None
        ),
        "budget_remaining": None if provider.value == "duckduckgo" else 0,
    }
    broker._reachability.get_all.return_value = {
        "duckduckgo": {
            "probes": {
                "local": {
                    "reachable": True,
                    "last_checked": NOW.timestamp() - 10,
                }
            }
        }
    }
    broker.budget_tracker.get_budget_limit.side_effect = lambda provider: (
        0 if provider.value == "duckduckgo" else 100
    )
    broker.spend_repository.provider_summary.side_effect = (
        lambda provider, budget_limit: {
            "remaining": None if budget_limit == 0 else 0,
            "argus_estimated_charge": 0,
            "uncertain_charge": 0,
            "provider_snapshot": None,
        }
    )
    _configure_public_evidence(broker)

    refresh_operational_status(
        service,
        broker=broker,
        repository=repository,
        browser_status={
            "declared": True,
            "available": False,
            "loaded": False,
            "degraded_reason": "browser_artifact_unavailable",
            "processes": 0,
            "memory_bytes": 0,
            "process_restarts": 2,
            "metrics_source": "process_memory_since_start",
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
    assert gauges["browser_processes"] == {
        "value": 0,
        "state": "healthy",
        "source": "process_memory_since_start",
    }
    assert gauges["browser_memory_bytes"] == {
        "value": 0,
        "state": "healthy",
        "source": "process_memory_since_start",
    }
    assert gauges["process_restarts"] == {
        "value": 2,
        "state": "healthy",
        "source": "process_memory_since_start",
    }


def test_disconnected_browser_snapshot_cannot_render_runtime_healthy():
    from argus.operations.status import refresh_operational_status

    service = _service()
    refresh_operational_status(
        service,
        broker=_runtime_broker(),
        repository=_runtime_repository(),
        browser_status={
            "declared": True,
            "available": True,
            "loaded": False,
            "runtime_state": "healthy",
            "runtime_reason": None,
            "processes": 0,
            "memory_bytes": 0,
            "process_restarts": 0,
        },
        now=NOW,
    )

    browser = service.full_status()["dependencies"]["browser"]
    assert browser["state"] == "degraded"
    assert browser["reason"] == "browser_disconnected"
    assert browser["source"] == "browser_runtime"


def test_accounting_failure_cannot_render_healthy_zero_uncertainty():
    from argus.operations.status import refresh_operational_status

    service = _service()
    broker = _runtime_broker()
    broker.spend_repository.provider_summary.side_effect = RuntimeError("ledger down")

    refresh_operational_status(
        service,
        broker=broker,
        repository=_runtime_repository(),
        now=NOW,
    )

    gauge = service.full_status()["metrics"]["gauges"]["accounting_uncertain_charge"]
    assert gauge == {
        "value": None,
        "state": "unknown",
        "source": "accounting_ledger",
    }
    balance = service.full_status()["providers"]["duckduckgo"]["observations"][
        "balance"
    ]
    assert balance["state"] == "unknown"


def test_mixed_accounting_reconciliation_preserves_success_and_uncertainty():
    from argus.operations.status import refresh_operational_status

    service = _service()
    broker = _runtime_broker()
    original_status = broker.get_provider_status.side_effect

    def provider_status(provider):
        if provider.value == "brave":
            return {
                "provider": "brave",
                "config_status": "enabled",
                "effective_status": "enabled",
                "health": {
                    "consecutive_failures": 0,
                    "last_success": NOW.timestamp(),
                    "last_failure": None,
                    "disabled_until": None,
                },
            }
        return original_status(provider)

    broker.get_provider_status.side_effect = provider_status
    _configure_public_evidence(broker)

    def summary(provider, budget_limit):
        if provider.value == "brave":
            raise RuntimeError("ledger unavailable")
        return {
            "remaining": None,
            "argus_estimated_charge": 0,
            "uncertain_charge": 0,
            "provider_snapshot": None,
        }

    broker.spend_repository.provider_summary.side_effect = summary
    refresh_operational_status(
        service,
        broker=broker,
        repository=_runtime_repository(),
        now=NOW,
    )

    status = service.full_status()
    assert (
        status["providers"]["duckduckgo"]["observations"]["balance"]["state"]
        == "healthy"
    )
    assert status["providers"]["brave"]["observations"]["balance"]["state"] == (
        "unknown"
    )
    assert status["metrics"]["gauges"]["accounting_uncertain_charge"]["state"] == (
        "unknown"
    )


def test_backup_and_restore_are_separate_typed_admin_observations():
    from argus.operations.status import refresh_operational_status

    service = _service()
    refresh_operational_status(
        service,
        broker=_runtime_broker(),
        repository=_runtime_repository(),
        recovery_status={
            "state": "degraded",
            "schema_promotion_allowed": False,
            "reasons": ["restore_stale"],
            "backup": {
                "completed_at": NOW.isoformat(),
                "age_seconds": 0,
                "fresh": True,
                "scope_complete": True,
            },
            "restore": {
                "verified_at": (NOW - timedelta(days=40)).isoformat(),
                "age_seconds": 40 * 86400,
                "fresh": False,
                "verified": True,
            },
        },
        now=NOW,
    )

    dependencies = service.full_status()["dependencies"]
    assert dependencies["backup"]["state"] == "healthy"
    assert dependencies["backup"]["source"] == "recovery_evidence"
    assert dependencies["restore"]["state"] == "degraded"
    assert dependencies["restore"]["reason"] == "restore_stale"


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-07-23T12:00:00Z", "2026-07-23T12:00:00Z"),
        ("2026-07-23T05:00:00-07:00", "2026-07-23T12:00:00Z"),
    ],
)
def test_recovery_evidence_preserves_valid_iso_timestamp(timestamp, expected):
    from argus.operations.status import refresh_operational_status

    service = _service()
    refresh_operational_status(
        service,
        broker=_runtime_broker(),
        repository=_runtime_repository(),
        recovery_status={
            "state": "ready",
            "schema_promotion_allowed": True,
            "reasons": [],
            "backup": {
                "completed_at": timestamp,
                "fresh": True,
                "scope_complete": True,
            },
            "restore": {
                "verified_at": timestamp,
                "fresh": True,
                "verified": True,
            },
        },
        now=NOW,
    )

    dependencies = service.full_status()["dependencies"]
    assert dependencies["backup"]["details"]["evidence_at"] == expected
    assert dependencies["restore"]["details"]["evidence_at"] == expected


@pytest.mark.parametrize("timestamp", [None, "not-a-time", "2026-07-23T12:00:00"])
def test_missing_or_invalid_recovery_timestamp_is_unknown(timestamp):
    from argus.operations.status import refresh_operational_status

    service = _service()
    backup = {"fresh": False, "scope_complete": True}
    restore = {"fresh": False, "verified": True}
    if timestamp is not None:
        backup["completed_at"] = timestamp
        restore["verified_at"] = timestamp
    refresh_operational_status(
        service,
        broker=_runtime_broker(),
        repository=_runtime_repository(),
        recovery_status={
            "state": "degraded",
            "schema_promotion_allowed": False,
            "reasons": ["backup_stale"],
            "backup": backup,
            "restore": restore,
        },
        now=NOW,
    )

    dependencies = service.full_status()["dependencies"]
    assert dependencies["backup"]["state"] == "unknown"
    assert dependencies["restore"]["state"] == "unknown"
    assert "evidence_at" not in dependencies["backup"].get("details", {})


def test_repository_refresh_updates_actual_backend_identity():
    from argus.operations.status import (
        create_operational_status,
        refresh_operational_status,
    )

    service = create_operational_status({})
    assert service.authority["backend"] == "sqlite"

    repository = MagicMock()
    repository.operational_status.return_value = {
        "backend": "postgresql",
        "connected": True,
        "schema_head": "0006_maya_outbox",
        "outbox": {"counts": {}},
    }
    broker = MagicMock()

    refresh_operational_status(
        service,
        broker=broker,
        repository=repository,
        now=NOW,
    )

    assert service.full_status()["authority"]["backend"] == "postgresql"


def test_fresh_provider_without_health_evidence_is_unknown_and_unready():
    from argus.operations.status import refresh_operational_status

    service = _service()
    broker = _runtime_broker()
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

    refresh_operational_status(
        service,
        broker=broker,
        repository=_runtime_repository(),
        browser_status={"available": True, "loaded": False},
        recovery_status={
            "state": "ready",
            "schema_promotion_allowed": True,
        },
        now=NOW,
    )
    service.observe_dependency(
        "maya",
        state="disabled",
        source="runtime_config",
        ttl=timedelta(minutes=5),
    )

    status = service.full_status()

    assert (
        status["providers"]["duckduckgo"]["observations"]["health"]["state"]
        == "unknown"
    )
    assert (
        status["providers"]["duckduckgo"]["observations"]["cooldown"]["state"]
        == "unknown"
    )
    assert status["status"] == "unready"
    assert status["reason_codes"] == ["retrieval_path"]


def test_expired_reachability_probe_renders_unknown_not_failed():
    from argus.operations.status import refresh_operational_status

    service = _service()
    broker = _runtime_broker()
    evidence = broker.operational_provider_evidence()
    evidence["duckduckgo"]["reachability"]["probes"]["local"].update(
        {
            "reachable": False,
            "expires_at": NOW.timestamp() - 1,
            "stale": True,
        }
    )
    broker.operational_provider_evidence.side_effect = None
    broker.operational_provider_evidence.return_value = evidence

    refresh_operational_status(
        service,
        broker=broker,
        repository=_runtime_repository(),
        now=NOW,
    )

    reachability = service.full_status()["providers"]["duckduckgo"]["observations"][
        "reachability"
    ]
    assert reachability["state"] == "unknown"
    assert reachability["reason"] == "reachability_evidence_expired"


def test_reachability_source_and_time_use_same_current_probe_subset():
    from argus.operations.status import refresh_operational_status

    service = _service()
    broker = _runtime_broker()
    evidence = broker.operational_provider_evidence()
    evidence["duckduckgo"]["reachability"]["probes"] = {
        "stale-local": {
            "reachable": False,
            "last_checked": NOW.timestamp() - 5,
            "source": "provider_execution",
            "stale": True,
        },
        "current-worker": {
            "reachable": True,
            "last_checked": NOW.timestamp() - 10,
            "source": "background_probe",
            "stale": False,
        },
        "current-failed-execution": {
            "reachable": False,
            "last_checked": NOW.timestamp() - 7,
            "source": "provider_execution",
            "stale": False,
        },
    }
    broker.operational_provider_evidence.side_effect = None
    broker.operational_provider_evidence.return_value = evidence

    refresh_operational_status(
        service,
        broker=broker,
        repository=_runtime_repository(),
        now=NOW,
    )

    reachability = service.full_status()["providers"]["duckduckgo"]["observations"][
        "reachability"
    ]
    assert reachability["state"] == "healthy"
    assert reachability["source"] == "reachability_probe"
    assert reachability["observed_at"] == "2026-07-23T11:59:50Z"


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
        "health": (
            {
                "consecutive_failures": 0,
                "last_success": NOW.timestamp() + 5,
                "last_failure": None,
                "disabled_until": None,
            }
            if provider.value == "duckduckgo"
            else None
        ),
        "budget_remaining": None,
    }
    broker._reachability.get_all.return_value = {
        "duckduckgo": {
            "probes": {
                "local": {
                    "reachable": True,
                    "last_checked": NOW.timestamp() + 5,
                }
            }
        }
    }
    broker.budget_tracker.get_budget_limit.return_value = 0
    broker.spend_repository.provider_summary.return_value = {
        "remaining": None,
        "argus_estimated_charge": 0,
        "uncertain_charge": 0,
        "provider_snapshot": None,
    }
    _configure_public_evidence(broker)

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
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))

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

    repository = create_search_ledger_repository(f"sqlite:///{tmp_path / 'status.db'}")

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

    response = client.get("/api/ready", headers={"X-Request-ID": unsafe})

    assert response.status_code == 503
    messages = "\n".join(
        str(call.args[0]) % call.args[1:] for call in logged.call_args_list
    )
    assert "http_request" in messages
    assert "route=/api/ready" in messages
    assert f"request_id={response.headers['x-request-id']}" in messages
    assert "must-not-leak" not in messages
    assert "secret.example" not in messages


def test_extract_application_logs_do_not_include_target_url_or_query(
    monkeypatch, caplog
):
    import logging

    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.extraction.models import ExtractedContent, ExtractorName

    target = "https://private.example/report?token=must-not-leak"
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(
        "argus.api.routes_extract.extract_url",
        AsyncMock(
            return_value=ExtractedContent(
                url=target,
                text="safe content",
                word_count=2,
                extractor=ExtractorName.TRAFILATURA,
            )
        ),
    )

    response = TestClient(
        create_app(
            broker=MagicMock(),
            search_repository=MagicMock(),
            operational_status=_service(),
        )
    ).post("/api/extract", json={"url": target})

    assert response.status_code == 200
    assert target not in caplog.text
    assert "must-not-leak" not in caplog.text
    assert "private.example" not in caplog.text


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
        "effective_status": (
            "enabled"
            if provider.value in {"duckduckgo", "brave"}
            else "disabled_by_config"
        ),
    }
    client = TestClient(create_app(broker=broker, operational_status=service))

    response = client.get("/api/provider-health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    brave = response.json()["providers"]["brave"]
    assert brave["state"] == "unready"
    assert brave["observations"]["reachability"]["reason"] == "probe_failed"


def test_liveness_is_exempt_from_request_rate_limits():
    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.api.rate_limit import RateLimiter

    client = TestClient(
        create_app(
            rate_limiter=RateLimiter(
                max_requests=1,
                window_seconds=60,
                exempt_paths=["/api/custom-probe"],
            ),
            operational_status=_service(),
        )
    )

    assert client.get("/api/live").status_code == 200
    assert client.get("/api/live").status_code == 200


@pytest.mark.parametrize("path", ["/api/live", "/api/health"])
def test_liveness_bypasses_application_metrics_and_logging(path, monkeypatch):
    from fastapi.testclient import TestClient

    import argus.api.main as api_main

    service = _service()
    service.metrics.request_started = MagicMock(
        side_effect=AssertionError("metrics lock must not be touched")
    )
    logged = MagicMock(side_effect=AssertionError("application log must not run"))
    monkeypatch.setattr(api_main.logger, "info", logged)

    response = TestClient(api_main.create_app(operational_status=service)).get(path)

    assert response.status_code == 200
    service.metrics.request_started.assert_not_called()
    logged.assert_not_called()


def test_async_background_probe_cannot_delay_liveness(monkeypatch):
    import asyncio
    import threading
    import time

    from fastapi.testclient import TestClient

    import argus.api.main as api_main
    from argus.broker.health import HealthTracker
    from argus.broker.reachability import ReachabilityMatrix
    from argus.broker.router import SearchBroker
    from argus.models import ProviderName, ProviderTrace
    from argus.providers.base import ProbeCapability

    entered = threading.Event()
    broker = _runtime_broker()

    class BlockingProvider:
        probe_capability = ProbeCapability.ASYNC_NATIVE

        def is_available(self):
            return True

        async def search(self, query):
            entered.set()
            await asyncio.sleep(0.5)
            return [], ProviderTrace(
                provider=ProviderName.DUCKDUCKGO,
                status="success",
            )

    broker._providers = {ProviderName.DUCKDUCKGO: BlockingProvider()}
    broker._egress_nodes = {}
    broker._reachability = ReachabilityMatrix()
    broker._health = HealthTracker()
    broker.refresh_provider_evidence = SearchBroker.refresh_provider_evidence.__get__(
        broker, SearchBroker
    )
    service = _service()
    service.metrics.request_started = MagicMock(
        side_effect=AssertionError("liveness telemetry must remain untouched")
    )
    logged = MagicMock(side_effect=AssertionError("liveness log must remain untouched"))
    monkeypatch.setattr(api_main.logger, "info", logged)

    with TestClient(
        api_main.create_app(
            broker=broker,
            search_repository=_runtime_repository(),
            operational_status=service,
        )
    ) as client:
        assert entered.wait(timeout=1)
        started = time.monotonic()
        response = client.get("/api/live")
        elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 0.2
    service.metrics.request_started.assert_not_called()
    logged.assert_not_called()


def test_blocking_only_probe_never_outlives_lifespan_cleanup(monkeypatch):
    import asyncio
    import threading
    import time

    from fastapi.testclient import TestClient

    import argus.extraction.playwright_extractor as extractor
    from argus.api.main import create_app
    from argus.broker.health import HealthTracker
    from argus.broker.reachability import ReachabilityMatrix
    from argus.broker.router import SearchBroker
    from argus.models import ProviderName, ProviderTrace
    from argus.providers.base import ProbeCapability

    refresh_started = threading.Event()
    external_active = threading.Event()
    release_external = threading.Event()
    cleanup: list[str] = []

    class SignaledMatrix(ReachabilityMatrix):
        async def probe_all(self, *args, **kwargs):
            refresh_started.set()
            return await super().probe_all(*args, **kwargs)

    class BlockingOnlyProvider:
        probe_capability = ProbeCapability.BLOCKING_UNSUPPORTED
        calls = 0

        def is_available(self):
            return True

        async def search(self, query):
            self.calls += 1
            external_active.set()
            try:
                await asyncio.to_thread(release_external.wait)
            finally:
                external_active.clear()
            return [], ProviderTrace(
                provider=ProviderName.DUCKDUCKGO,
                status="success",
            )

    provider = BlockingOnlyProvider()
    broker = _runtime_broker()
    broker._providers = {ProviderName.DUCKDUCKGO: provider}
    broker._egress_nodes = {}
    broker._reachability = SignaledMatrix()
    broker._health = HealthTracker()
    broker.refresh_provider_evidence = SearchBroker.refresh_provider_evidence.__get__(
        broker, SearchBroker
    )
    broker.budget_tracker.close.side_effect = lambda: cleanup.append("budget")

    async def close_browser():
        cleanup.append("browser")

    monkeypatch.setattr(extractor, "close_browser", close_browser)
    app = create_app(
        broker=broker,
        search_repository=_runtime_repository(),
        operational_status=_service(),
    )

    started = time.monotonic()
    try:
        with TestClient(app):
            assert refresh_started.wait(timeout=1)
        elapsed = time.monotonic() - started

        assert provider.calls == 0
        assert external_active.is_set() is False
        assert cleanup == ["browser", "budget"]
        assert elapsed < 0.5
    finally:
        release_external.set()


def test_async_native_probe_is_cancelled_before_cleanup(monkeypatch):
    import asyncio
    import threading

    from fastapi.testclient import TestClient

    import argus.extraction.playwright_extractor as extractor
    from argus.api.main import create_app
    from argus.broker.health import HealthTracker
    from argus.broker.reachability import ReachabilityMatrix
    from argus.broker.router import SearchBroker
    from argus.models import ProviderName
    from argus.providers.base import ProbeCapability

    entered = threading.Event()
    cleanup: list[str] = []

    class AsyncNativeProvider:
        probe_capability = ProbeCapability.ASYNC_NATIVE

        def is_available(self):
            return True

        async def search(self, query):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.append("probe")

    broker = _runtime_broker()
    broker._providers = {ProviderName.DUCKDUCKGO: AsyncNativeProvider()}
    broker._egress_nodes = {}
    broker._reachability = ReachabilityMatrix()
    broker._health = HealthTracker()
    broker.refresh_provider_evidence = SearchBroker.refresh_provider_evidence.__get__(
        broker, SearchBroker
    )
    broker.budget_tracker.close.side_effect = lambda: cleanup.append("budget")

    async def close_browser():
        cleanup.append("browser")

    monkeypatch.setattr(extractor, "close_browser", close_browser)

    with TestClient(
        create_app(
            broker=broker,
            search_repository=_runtime_repository(),
            operational_status=_service(),
        )
    ):
        assert entered.wait(timeout=1)

    assert cleanup == ["probe", "browser", "budget"]


def test_provider_evidence_snapshot_and_probe_updates_share_owner_loop():
    import threading

    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.broker.health import HealthTracker
    from argus.broker.reachability import ReachabilityMatrix
    from argus.broker.router import SearchBroker
    from argus.models import ProviderName, ProviderTrace
    from argus.providers.base import ProbeCapability

    owner_threads: dict[str, int] = {}
    probe_recorded = threading.Event()

    class OwnerHealthTracker(HealthTracker):
        def record_success(self, provider):
            owner_threads["health"] = threading.get_ident()
            super().record_success(provider)
            probe_recorded.set()

    class AsyncNativeProvider:
        probe_capability = ProbeCapability.ASYNC_NATIVE

        def is_available(self):
            return True

        async def search(self, query):
            owner_threads["probe"] = threading.get_ident()
            return [], ProviderTrace(
                provider=ProviderName.DUCKDUCKGO,
                status="success",
            )

    broker = _runtime_broker()
    broker._providers = {ProviderName.DUCKDUCKGO: AsyncNativeProvider()}
    broker._egress_nodes = {}
    broker._reachability = ReachabilityMatrix()
    broker._health = OwnerHealthTracker()
    broker.refresh_provider_evidence = SearchBroker.refresh_provider_evidence.__get__(
        broker, SearchBroker
    )

    def operational_provider_evidence():
        owner_threads["snapshot"] = threading.get_ident()
        return {}

    broker.operational_provider_evidence.side_effect = operational_provider_evidence

    with TestClient(
        create_app(
            broker=broker,
            search_repository=_runtime_repository(),
            operational_status=_service(),
        )
    ):
        assert probe_recorded.wait(timeout=1)

    assert owner_threads["snapshot"] == owner_threads["probe"]
    assert owner_threads["probe"] == owner_threads["health"]
    assert owner_threads["snapshot"] != threading.get_ident()


def test_blocked_repository_status_stops_before_lifespan_cleanup(monkeypatch):
    import threading
    import time

    from fastapi.testclient import TestClient

    import argus.extraction.playwright_extractor as extractor
    from argus.api.lifecycle import LifecycleCapability
    from argus.api.main import create_app

    entered = threading.Event()
    release_external = threading.Event()
    cleanup: list[str] = []
    repository = _runtime_repository()
    repository.lifecycle_capability = LifecycleCapability.COOPERATIVE_BOUNDED

    def operational_status(*, now=None, stop_event=None):
        entered.set()
        (stop_event or release_external).wait()
        cleanup.append("repository-status-stopped")
        return repository.operational_status.return_value

    repository.operational_status.side_effect = operational_status
    repository.close.side_effect = lambda: cleanup.append("repository")
    broker = _runtime_broker()
    broker.budget_tracker.close.side_effect = lambda: cleanup.append("budget")

    async def close_browser():
        cleanup.append("browser")

    monkeypatch.setattr(extractor, "close_browser", close_browser)
    service = _service()
    started = time.monotonic()
    try:
        with TestClient(
            create_app(
                broker=broker,
                search_repository=repository,
                operational_status=service,
            )
        ):
            assert entered.wait(timeout=1)
            status_before_shutdown = service.full_status()
        elapsed = time.monotonic() - started

        assert elapsed < 0.5
        assert not any(
            thread.name.endswith("-lifecycle") for thread in threading.enumerate()
        )
        assert service.full_status() == status_before_shutdown
        assert cleanup == [
            "repository-status-stopped",
            "browser",
            "budget",
            "repository",
        ]
    finally:
        release_external.set()


def test_blocked_maya_dispatch_stops_before_lifespan_cleanup(
    monkeypatch,
):
    import threading
    import time
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import argus.config as config_module
    import argus.extraction.playwright_extractor as extractor
    import argus.persistence.maya_outbox as maya_outbox
    from argus.api.lifecycle import LifecycleCapability
    from argus.api.main import create_app

    entered = threading.Event()
    release_external = threading.Event()
    cleanup: list[str] = []
    capture = SimpleNamespace(
        endpoint="http://maya/captures",
        token="dedicated-token",
        timeout_seconds=1,
        batch_size=1,
        poll_seconds=1,
        acknowledged_retention_days=1,
    )
    monkeypatch.setattr(
        config_module,
        "get_config",
        lambda: SimpleNamespace(egress_nodes=[], maya_capture=capture),
    )

    class Dispatcher:
        lifecycle_capability = LifecycleCapability.COOPERATIVE_BOUNDED

        def __init__(self, *args, **kwargs):
            pass

        def run_once(self, *, stop_event=None):
            entered.set()
            (stop_event or release_external).wait()
            cleanup.append("maya-dispatch-stopped")
            return {}

    monkeypatch.setattr(maya_outbox, "MayaOutboxDispatcher", Dispatcher)
    repository = _runtime_repository()
    repository.lifecycle_capability = LifecycleCapability.COOPERATIVE_BOUNDED
    repository.close.side_effect = lambda: cleanup.append("repository")
    broker = _runtime_broker()
    broker.budget_tracker.close.side_effect = lambda: cleanup.append("budget")

    async def close_browser():
        cleanup.append("browser")

    monkeypatch.setattr(extractor, "close_browser", close_browser)
    service = _service()
    started = time.monotonic()
    try:
        with TestClient(
            create_app(
                broker=broker,
                search_repository=repository,
                operational_status=service,
            )
        ):
            assert entered.wait(timeout=1)
            status_before_shutdown = service.full_status()
        elapsed = time.monotonic() - started

        assert elapsed < 0.5
        assert not any(
            thread.name.endswith("-lifecycle") for thread in threading.enumerate()
        )
        assert service.full_status() == status_before_shutdown
        assert cleanup == [
            "maya-dispatch-stopped",
            "browser",
            "budget",
            "repository",
        ]
    finally:
        release_external.set()


def test_slow_maya_lane_does_not_stale_authority_status(
    monkeypatch,
):
    import threading
    import time
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import argus.api.main as api_main
    import argus.config as config_module
    import argus.persistence.maya_outbox as maya_outbox
    from argus.api.lifecycle import LifecycleCapability

    monkeypatch.setattr(api_main, "_STATUS_REFRESH_SECONDS", 0.02)
    monkeypatch.setattr(api_main, "_MAYA_SLOW_SECONDS", 0.03)
    capture = SimpleNamespace(
        endpoint="http://maya/captures",
        token="dedicated-token",
        timeout_seconds=1,
        batch_size=100,
        poll_seconds=1,
        acknowledged_retention_days=1,
    )
    monkeypatch.setattr(
        config_module,
        "get_config",
        lambda: SimpleNamespace(egress_nodes=[], maya_capture=capture),
    )
    entered = threading.Event()

    class SlowDispatcher:
        dispatch_capability = LifecycleCapability.COOPERATIVE_BOUNDED

        def __init__(self, *args, **kwargs):
            pass

        def run_once(self, *, stop_event):
            entered.set()
            stop_event.wait()
            return {}

    monkeypatch.setattr(maya_outbox, "MayaOutboxDispatcher", SlowDispatcher)
    repository = _runtime_repository()
    refreshes = 0

    def operational_status(**kwargs):
        nonlocal refreshes
        refreshes += 1
        return {
            "backend": "postgresql",
            "connected": True,
            "schema_head": "0006_maya_outbox",
            "outbox": {"counts": {}},
        }

    repository.operational_status.side_effect = operational_status
    service = _service(production=True)

    with TestClient(
        api_main.create_app(
            broker=_runtime_broker(),
            search_repository=repository,
            operational_status=service,
        )
    ):
        assert entered.wait(timeout=1)
        deadline = time.monotonic() + 1
        while refreshes < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        status = service.full_status()

        assert refreshes >= 3
        assert status["dependencies"]["postgresql"]["state"] == "healthy"
        assert status["dependencies"]["outbox"]["state"] == "healthy"
        assert status["dependencies"]["maya"]["state"] == "degraded"
        assert status["dependencies"]["maya"]["reason"] == "delivery_in_progress"
        assert status["status"] == "degraded"


def test_blocked_maya_compaction_stops_before_lifespan_cleanup(
    monkeypatch,
):
    import threading
    import time
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import argus.config as config_module
    import argus.extraction.playwright_extractor as extractor
    import argus.persistence.maya_outbox as maya_outbox
    from argus.api.lifecycle import LifecycleCapability
    from argus.api.main import create_app

    entered = threading.Event()
    release_external = threading.Event()
    cleanup: list[str] = []
    capture = SimpleNamespace(
        endpoint="http://maya/captures",
        token="dedicated-token",
        timeout_seconds=1,
        batch_size=1,
        poll_seconds=1,
        acknowledged_retention_days=1,
    )
    monkeypatch.setattr(
        config_module,
        "get_config",
        lambda: SimpleNamespace(egress_nodes=[], maya_capture=capture),
    )

    class Dispatcher:
        lifecycle_capability = LifecycleCapability.COOPERATIVE_BOUNDED

        def __init__(self, *args, **kwargs):
            pass

        def run_once(self, *, stop_event=None):
            return {"acknowledged": 1}

    monkeypatch.setattr(maya_outbox, "MayaOutboxDispatcher", Dispatcher)
    repository = _runtime_repository()
    repository.lifecycle_capability = LifecycleCapability.COOPERATIVE_BOUNDED

    def compact_maya_outbox(*, stop_event=None, **kwargs):
        entered.set()
        (stop_event or release_external).wait()
        cleanup.append("maya-compaction-stopped")
        return 0

    repository.compact_maya_outbox.side_effect = compact_maya_outbox
    repository.close.side_effect = lambda: cleanup.append("repository")
    broker = _runtime_broker()
    broker.budget_tracker.close.side_effect = lambda: cleanup.append("budget")

    async def close_browser():
        cleanup.append("browser")

    monkeypatch.setattr(extractor, "close_browser", close_browser)
    service = _service()
    started = time.monotonic()
    try:
        with TestClient(
            create_app(
                broker=broker,
                search_repository=repository,
                operational_status=service,
            )
        ):
            assert entered.wait(timeout=1)
            status_before_shutdown = service.full_status()
        elapsed = time.monotonic() - started

        assert elapsed < 0.5
        assert not any(
            thread.name.endswith("-lifecycle") for thread in threading.enumerate()
        )
        assert service.full_status() == status_before_shutdown
        assert cleanup == [
            "maya-compaction-stopped",
            "browser",
            "budget",
            "repository",
        ]
    finally:
        release_external.set()


@pytest.mark.parametrize(
    "unsafe_operation",
    ["repository_status", "maya_dispatch", "maya_compaction"],
)
def test_uncooperative_background_operation_is_never_launched(
    unsafe_operation,
    monkeypatch,
):
    import threading
    import time
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import argus.config as config_module
    import argus.persistence.maya_outbox as maya_outbox
    from argus.api.lifecycle import LifecycleCapability
    from argus.api.main import create_app

    release_external = threading.Event()
    calls = 0
    capture = SimpleNamespace(
        endpoint="http://maya/captures",
        token="dedicated-token",
        timeout_seconds=1,
        batch_size=1,
        poll_seconds=0.01,
        acknowledged_retention_days=1,
    )
    monkeypatch.setattr(
        config_module,
        "get_config",
        lambda: SimpleNamespace(egress_nodes=[], maya_capture=capture),
    )
    repository = _runtime_repository()

    def never_return(**kwargs):
        nonlocal calls
        calls += 1
        release_external.wait()
        return {}

    if unsafe_operation == "repository_status":
        repository.operational_status_capability = LifecycleCapability.BLOCKING_UNSAFE
        repository.operational_status.side_effect = never_return
    elif unsafe_operation == "maya_compaction":
        repository.compaction_capability = LifecycleCapability.BLOCKING_UNSAFE
        repository.compact_maya_outbox.side_effect = never_return

    class Dispatcher:
        dispatch_capability = (
            LifecycleCapability.BLOCKING_UNSAFE
            if unsafe_operation == "maya_dispatch"
            else LifecycleCapability.COOPERATIVE_BOUNDED
        )

        def __init__(self, *args, **kwargs):
            pass

        def run_once(self, *, stop_event=None):
            if unsafe_operation == "maya_dispatch":
                return never_return()
            return {}

    monkeypatch.setattr(maya_outbox, "MayaOutboxDispatcher", Dispatcher)
    try:
        with TestClient(
            create_app(
                broker=_runtime_broker(),
                search_repository=repository,
                operational_status=_service(),
            )
        ):
            time.sleep(0.05)
        assert calls == 0
    finally:
        release_external.set()


def test_startup_remains_initialized_across_runtime_authority_loss_and_recovery():
    from argus.operations.status import refresh_operational_status

    service = _service()
    repository = _runtime_repository()
    broker = _runtime_broker()
    refresh_operational_status(service, broker=broker, repository=repository, now=NOW)
    assert service.startup_status()["status"] == "initialized"

    repository.operational_status.side_effect = RuntimeError("database lost")
    refresh_operational_status(
        service,
        broker=broker,
        repository=repository,
        now=NOW + timedelta(seconds=10),
    )

    assert service.startup_status()["status"] == "initialized"
    readiness = service.readiness_status()
    assert readiness["status"] == "unready"
    assert readiness["reason_codes"] == ["postgresql", "schema", "outbox"]

    repository.operational_status.side_effect = None
    repository.operational_status.return_value = {
        "backend": "postgresql",
        "connected": True,
        "schema_head": "0006_maya_outbox",
        "outbox": {"counts": {}},
    }
    refresh_operational_status(
        service,
        broker=broker,
        repository=repository,
        now=NOW + timedelta(seconds=20),
    )

    assert service.startup_status()["status"] == "initialized"
    assert service.readiness_status()["status"] != "unready"


def test_missing_manifest_does_not_invent_optional_capabilities(tmp_path):
    from argus.operations.status import create_operational_status

    service = create_operational_status(
        {"ARGUS_RUNTIME_MANIFEST": str(tmp_path / "missing.json")}
    )

    assert service.full_status()["capabilities"] == {}


def test_sqlite_marks_postgresql_not_applicable():
    from argus.operations.status import (
        create_operational_status,
        refresh_operational_status,
    )

    service = create_operational_status({}, clock=lambda: NOW)
    broker = _runtime_broker()
    repository = MagicMock()
    repository.operational_status.return_value = {
        "backend": "sqlite",
        "connected": True,
        "schema_head": "sqlite-managed",
        "outbox": {"counts": {}},
    }

    refresh_operational_status(
        service,
        broker=broker,
        repository=repository,
        now=NOW,
    )

    postgresql = service.full_status()["dependencies"]["postgresql"]
    assert postgresql["state"] == "disabled"
    assert postgresql["reason"] == "not_applicable_sqlite"


def test_background_and_request_share_one_broker_initialization():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from argus.api.main import _build_broker_provider

    entered = threading.Event()
    release = threading.Event()
    second_entry = threading.Event()
    calls = 0
    calls_lock = threading.Lock()
    broker = object()

    def factory():
        nonlocal calls
        with calls_lock:
            calls += 1
            if calls > 1:
                second_entry.set()
        entered.set()
        release.wait(timeout=1)
        return broker

    provider = _build_broker_provider(None, factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(provider)
        assert entered.wait(timeout=1)
        second = pool.submit(provider)
        raced = second_entry.wait(timeout=0.1)
        release.set()

    assert raced is False
    assert first.result() is broker
    assert second.result() is broker
    assert calls == 1


def test_slow_dependency_initialization_does_not_block_liveness():
    import threading
    import time

    from fastapi.testclient import TestClient

    from argus.api.main import create_app

    gate = threading.Event()
    called = threading.Event()

    def slow_factory():
        called.set()
        gate.wait(timeout=2)
        raise RuntimeError("dependency unavailable")

    service = _service()
    started = time.monotonic()
    with TestClient(
        create_app(
            broker_factory=slow_factory,
            operational_status=service,
        ),
        raise_server_exceptions=False,
    ) as client:
        entered_after = time.monotonic() - started
        live = client.get("/api/live")
        gate.set()

    assert entered_after < 0.5
    assert live.status_code == 200
    assert called.is_set() is False


def test_unbounded_repository_constructor_is_not_admitted_to_lifespan(
    monkeypatch,
):
    import threading
    import time
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import argus.api.main as api_main
    import argus.config as config_module

    called = threading.Event()
    release = threading.Event()
    capture = SimpleNamespace(endpoint="", token="", poll_seconds=5)
    config = SimpleNamespace(
        db_url="postgresql://argus@example.invalid/argus",
        egress_nodes=[],
        maya_capture=capture,
    )
    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setattr(config_module, "get_config", lambda: config)

    def blocked_constructor():
        called.set()
        release.wait()
        return _runtime_repository()

    monkeypatch.setattr(
        api_main,
        "create_search_ledger_repository",
        blocked_constructor,
    )
    started = time.monotonic()
    try:
        with TestClient(
            api_main.create_app(
                broker=_runtime_broker(),
                operational_status=_service(production=True),
            ),
            raise_server_exceptions=False,
        ) as client:
            assert client.get("/api/live").status_code == 200
        assert time.monotonic() - started < 0.5
        assert called.is_set() is False
    finally:
        release.set()


def test_bounded_production_repository_constructor_recovers(
    monkeypatch,
):
    import threading
    import time
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import argus.api.main as api_main
    import argus.config as config_module
    from argus.api.lifecycle import LifecycleCapability

    capture = SimpleNamespace(endpoint="", token="", poll_seconds=5)
    config = SimpleNamespace(
        db_url="postgresql://argus@example.invalid/argus",
        egress_nodes=[],
        maya_capture=capture,
    )
    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setattr(config_module, "get_config", lambda: config)
    monkeypatch.setattr(api_main, "_AUTHORITY_RETRY_SECONDS", 0.01)
    recovered = threading.Event()
    attempts = 0
    repository = _runtime_repository()

    def repository_constructor():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary database failure")
        recovered.set()
        return repository

    repository_constructor.lifecycle_capability = LifecycleCapability.FINITE_BOUNDED
    monkeypatch.setattr(
        api_main,
        "create_search_ledger_repository",
        repository_constructor,
    )

    with TestClient(
        api_main.create_app(
            broker=_runtime_broker(),
            operational_status=_service(production=True),
        ),
        raise_server_exceptions=False,
    ):
        assert recovered.wait(timeout=1)
        deadline = time.monotonic() + 1
        while (
            repository.operational_status.call_count == 0
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)

    assert attempts == 2
    assert repository.operational_status.call_count > 0


def test_operational_background_workers_shutdown_cleanly():
    from fastapi.testclient import TestClient

    from argus.api.main import create_app

    broker = MagicMock()
    repository = MagicMock()
    repository.operational_status.return_value = {
        "backend": "postgresql",
        "connected": True,
        "schema_head": "0006_maya_outbox",
        "outbox": {"counts": {}},
    }
    service = _service()

    with TestClient(
        create_app(
            broker=broker,
            search_repository=repository,
            operational_status=service,
        )
    ) as client:
        assert client.get("/api/live").status_code == 200


def test_broker_construction_failure_recovers_without_process_restart(
    monkeypatch,
):
    import threading
    import time

    from fastapi.testclient import TestClient

    import argus.api.main as api_main
    from argus.api.lifecycle import LifecycleCapability
    from argus.api.rate_limit import RateLimiter

    monkeypatch.setattr(api_main, "_AUTHORITY_RETRY_SECONDS", 0.01, raising=False)
    failed = threading.Event()
    allow_recovery = threading.Event()
    attempts = 0
    broker = _runtime_broker()

    def broker_factory():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            failed.set()
            raise RuntimeError("initial broker construction failed")
        allow_recovery.wait(timeout=2)
        return broker

    broker_factory.lifecycle_capability = LifecycleCapability.COOPERATIVE_BOUNDED
    service = _service()
    app = api_main.create_app(
        broker_factory=broker_factory,
        rate_limiter=RateLimiter(max_requests=10_000),
        search_repository=_runtime_repository(),
        operational_status=service,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        assert failed.wait(timeout=1)
        deadline = time.monotonic() + 1
        while (
            service.startup_status()["status"] != "failed"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert client.get("/api/ready").status_code == 503

        allow_recovery.set()
        deadline = time.monotonic() + 2
        response = client.get("/api/ready")
        while response.status_code != 200 and time.monotonic() < deadline:
            time.sleep(0.01)
            response = client.get("/api/ready")

        assert response.status_code == 200
        assert service.full_status()["dependencies"]["postgresql"]["state"] == "healthy"
        assert attempts >= 2


def test_repository_construction_failure_recovers_without_process_restart(
    monkeypatch,
):
    import threading
    import time
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import argus.api.main as api_main
    import argus.config as config_module
    from argus.api.lifecycle import LifecycleCapability
    from argus.api.rate_limit import RateLimiter

    capture = SimpleNamespace(endpoint="", token="", poll_seconds=5)
    config = SimpleNamespace(
        db_url="postgresql://argus@example.invalid/argus",
        egress_nodes=[],
        maya_capture=capture,
    )
    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setattr(config_module, "get_config", lambda: config)
    monkeypatch.setattr(api_main, "_AUTHORITY_RETRY_SECONDS", 0.01, raising=False)
    failed = threading.Event()
    allow_recovery = threading.Event()
    attempts = 0
    repository = _runtime_repository()

    def repository_factory():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            failed.set()
            raise RuntimeError("initial repository construction failed")
        allow_recovery.wait(timeout=2)
        return repository

    repository_factory.lifecycle_capability = LifecycleCapability.FINITE_BOUNDED
    monkeypatch.setattr(
        api_main,
        "create_search_ledger_repository",
        repository_factory,
    )
    service = _service(production=True)
    app = api_main.create_app(
        broker=_runtime_broker(),
        rate_limiter=RateLimiter(max_requests=10_000),
        operational_status=service,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        assert failed.wait(timeout=1)
        deadline = time.monotonic() + 1
        while (
            service.startup_status()["status"] != "failed"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert client.get("/api/ready").status_code == 503

        allow_recovery.set()
        deadline = time.monotonic() + 2
        response = client.get("/api/ready")
        while response.status_code != 200 and time.monotonic() < deadline:
            time.sleep(0.01)
            response = client.get("/api/ready")

        assert response.status_code == 200
        assert service.full_status()["authority"]["backend"] == "postgresql"
        assert attempts >= 2


def test_maya_empty_poll_preserves_unknown_or_last_delivery_evidence():
    service = _service()

    service.observe_maya_delivery(
        {},
        ttl=timedelta(seconds=30),
        observed_at=NOW,
    )
    unknown = service.full_status()["dependencies"]["maya"]
    assert unknown["state"] == "unknown"

    service.observe_maya_delivery(
        {"acknowledged": 1},
        ttl=timedelta(seconds=30),
        observed_at=NOW,
    )
    acknowledged = service.full_status()["dependencies"]["maya"]
    service.observe_maya_delivery(
        {},
        ttl=timedelta(seconds=30),
        observed_at=NOW + timedelta(seconds=5),
    )
    after_empty = service.full_status()["dependencies"]["maya"]

    assert acknowledged["state"] == "healthy"
    assert after_empty == acknowledged


@pytest.mark.parametrize(
    ("outcomes", "reason"),
    [
        ({"retried": 1}, "delivery_retried"),
        ({"dead_lettered": 1}, "delivery_dead_lettered"),
        ({"lease_lost": 1}, "delivery_lease_lost"),
        (
            {"acknowledged": 1, "retried": 1},
            "delivery_retried",
        ),
    ],
)
def test_maya_failure_outcomes_degrade_delivery(outcomes, reason):
    service = _service()

    service.observe_maya_delivery(
        outcomes,
        ttl=timedelta(seconds=30),
        observed_at=NOW,
    )

    maya = service.full_status()["dependencies"]["maya"]
    assert maya["state"] == "degraded"
    assert maya["reason"] == reason


def test_missing_production_maya_configuration_is_degraded():
    service = _service(production=True)

    service.observe_maya_configuration(
        configured=False,
        ttl=timedelta(minutes=5),
    )

    maya = service.full_status()["dependencies"]["maya"]
    assert maya["state"] == "degraded"
    assert maya["reason"] == "maya_delivery_not_configured"


@pytest.mark.parametrize(
    ("outcomes", "expected_state"),
    [
        ({}, "unknown"),
        ({"acknowledged": 1}, "healthy"),
        ({"retried": 1}, "degraded"),
    ],
)
def test_maya_worker_uses_real_dispatcher_outcomes(
    monkeypatch,
    outcomes,
    expected_state,
):
    import threading
    import time
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import argus.api.main as api_main
    import argus.config as config_module
    import argus.persistence.maya_outbox as maya_outbox
    from argus.api.lifecycle import LifecycleCapability

    capture = SimpleNamespace(
        endpoint="http://maya/captures",
        token="dedicated-token",
        timeout_seconds=1,
        batch_size=1,
        poll_seconds=0.01,
        acknowledged_retention_days=1,
    )
    config = SimpleNamespace(egress_nodes=[], maya_capture=capture)
    monkeypatch.setattr(config_module, "get_config", lambda: config)
    calls = 0

    class Dispatcher:
        lifecycle_capability = LifecycleCapability.COOPERATIVE_BOUNDED

        def __init__(self, *args, **kwargs):
            pass

        def run_once(self, *, stop_event=None):
            nonlocal calls
            calls += 1
            return outcomes

    monkeypatch.setattr(maya_outbox, "MayaOutboxDispatcher", Dispatcher)
    service = _service(production=True)
    repository = _runtime_repository()

    with TestClient(
        api_main.create_app(
            broker=_runtime_broker(),
            search_repository=repository,
            operational_status=service,
        )
    ):
        deadline = time.monotonic() + 1
        while calls == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls > 0
        assert repository.operational_status.call_count > 0
        assert isinstance(
            repository.operational_status.call_args.kwargs["stop_event"],
            threading.Event,
        )
        assert service.full_status()["dependencies"]["maya"]["state"] == expected_state


def test_outbox_compaction_failure_does_not_rewrite_maya_delivery_evidence(
    monkeypatch,
):
    import time
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import argus.api.main as api_main
    import argus.config as config_module
    import argus.persistence.maya_outbox as maya_outbox
    from argus.api.lifecycle import LifecycleCapability

    capture = SimpleNamespace(
        endpoint="http://maya/captures",
        token="dedicated-token",
        timeout_seconds=1,
        batch_size=1,
        poll_seconds=0.01,
        acknowledged_retention_days=1,
    )
    monkeypatch.setattr(
        config_module,
        "get_config",
        lambda: SimpleNamespace(egress_nodes=[], maya_capture=capture),
    )
    delivered = False

    class Dispatcher:
        lifecycle_capability = LifecycleCapability.COOPERATIVE_BOUNDED

        def __init__(self, *args, **kwargs):
            pass

        def run_once(self, *, stop_event=None):
            nonlocal delivered
            delivered = True
            return {"acknowledged": 1}

    monkeypatch.setattr(maya_outbox, "MayaOutboxDispatcher", Dispatcher)
    repository = _runtime_repository()
    repository.compact_maya_outbox.side_effect = RuntimeError("local compaction failed")
    service = _service(production=True)

    with TestClient(
        api_main.create_app(
            broker=_runtime_broker(),
            search_repository=repository,
            operational_status=service,
        )
    ):
        deadline = time.monotonic() + 1
        while not delivered and time.monotonic() < deadline:
            time.sleep(0.01)
        assert delivered is True
        assert service.full_status()["dependencies"]["maya"]["state"] == "healthy"
