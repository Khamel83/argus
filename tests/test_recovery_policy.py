import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


NOW = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)


def _valid_evidence() -> dict:
    return {
        "schema_version": 1,
        "backup": {
            "completed_at": (NOW - timedelta(hours=2)).isoformat(),
            "databases": ["atlas", "argus"],
            "globals": True,
            "manifest_sha256": "a" * 64,
            "archive_format": "custom",
            "outside_live_data": True,
            "unsafe_internal_path": "/srv/private/backups",
        },
        "restore": {
            "verified_at": (NOW - timedelta(days=3)).isoformat(),
            "databases": ["atlas", "argus"],
            "globals_validated": True,
            "schema_head": "0005_provider_spend",
            "backup_manifest_sha256": "a" * 64,
            "checks": {
                "schema": True,
                "row_counts": True,
                "integrity": True,
                "argus_read_path": True,
                "migration_compatible": True,
            },
            "scratch_database": "operator-secret-name",
        },
    }


def test_valid_recovery_evidence_allows_schema_promotion_without_leaking_paths(tmp_path):
    from argus.recovery.evidence import evaluate_recovery_evidence

    path = tmp_path / "recovery.json"
    path.write_text(json.dumps(_valid_evidence()), encoding="utf-8")

    status = evaluate_recovery_evidence(path, now=NOW)

    assert status["state"] == "ready"
    assert status["schema_promotion_allowed"] is True
    assert status["backup"]["databases"] == ["argus", "atlas"]
    assert status["restore"]["schema_head"] == "0005_provider_spend"
    assert status["restore"]["databases"] == ["argus", "atlas"]
    assert "unsafe_internal_path" not in json.dumps(status)
    assert "scratch_database" not in json.dumps(status)


def test_stale_backup_blocks_schema_change_but_not_code_only_promotion(tmp_path):
    from argus.recovery.evidence import evaluate_promotion_gate

    evidence = _valid_evidence()
    evidence["backup"]["completed_at"] = (NOW - timedelta(hours=49)).isoformat()
    path = tmp_path / "recovery.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    schema_gate = evaluate_promotion_gate(path, schema_change=True, now=NOW)
    code_gate = evaluate_promotion_gate(path, schema_change=False, now=NOW)

    assert schema_gate == {
        "allowed": False,
        "state": "blocked",
        "schema_change": True,
        "reasons": ["backup_stale"],
    }
    assert code_gate == {
        "allowed": True,
        "state": "degraded",
        "schema_change": False,
        "reasons": ["backup_stale"],
    }


def test_missing_or_malformed_evidence_fails_closed_for_schema_change(tmp_path):
    from argus.recovery.evidence import evaluate_promotion_gate

    missing = evaluate_promotion_gate(
        tmp_path / "missing.json", schema_change=True, now=NOW
    )
    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_text('{"backup":', encoding="utf-8")
    malformed = evaluate_promotion_gate(
        malformed_path, schema_change=True, now=NOW
    )

    assert missing["allowed"] is False
    assert missing["reasons"] == ["recovery_evidence_unavailable"]
    assert malformed["allowed"] is False
    assert malformed["reasons"] == ["recovery_evidence_invalid"]


def test_future_dated_evidence_fails_closed(tmp_path):
    from argus.recovery.evidence import evaluate_promotion_gate

    evidence = _valid_evidence()
    evidence["backup"]["completed_at"] = (NOW + timedelta(hours=1)).isoformat()
    path = tmp_path / "future.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    gate = evaluate_promotion_gate(path, schema_change=True, now=NOW)

    assert gate["allowed"] is False
    assert gate["reasons"] == ["recovery_evidence_invalid"]


def test_incomplete_backup_or_restore_checks_block_schema_promotion(tmp_path):
    from argus.recovery.evidence import evaluate_recovery_evidence

    evidence = _valid_evidence()
    evidence["backup"]["databases"] = ["argus"]
    evidence["restore"]["checks"]["argus_read_path"] = False
    path = tmp_path / "recovery.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    status = evaluate_recovery_evidence(path, now=NOW)

    assert status["schema_promotion_allowed"] is False
    assert status["reasons"] == [
        "backup_scope_incomplete",
        "restore_verification_failed",
    ]


def test_restore_schema_head_must_match_current_migration_head(tmp_path):
    from argus.recovery.evidence import evaluate_recovery_evidence

    evidence = _valid_evidence()
    evidence["restore"]["schema_head"] = "0003_request_routing_fields"
    path = tmp_path / "old-schema.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    status = evaluate_recovery_evidence(path, now=NOW)

    assert status["schema_promotion_allowed"] is False
    assert status["reasons"] == ["restore_verification_failed"]


def test_restore_evidence_must_be_bound_to_current_backup_manifest(tmp_path):
    from argus.recovery.evidence import evaluate_recovery_evidence

    evidence = _valid_evidence()
    evidence["restore"]["backup_manifest_sha256"] = "b" * 64
    path = tmp_path / "replayed-restore.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    status = evaluate_recovery_evidence(path, now=NOW)

    assert status["schema_promotion_allowed"] is False
    assert status["reasons"] == ["restore_verification_failed"]


@pytest.mark.asyncio
async def test_authenticated_health_detail_includes_sanitized_recovery_evidence(
    tmp_path,
    monkeypatch,
):
    from argus.api.routes_health import health_detail

    path = tmp_path / "recovery.json"
    path.write_text(json.dumps(_valid_evidence()), encoding="utf-8")
    monkeypatch.setenv("ARGUS_RECOVERY_EVIDENCE_PATH", str(path))
    broker = MagicMock()
    broker.get_provider_status.return_value = {"effective_status": "enabled"}
    broker.health_tracker.get_all_status.return_value = {}
    broker._reachability.get_all.return_value = {}

    payload = await health_detail(broker)

    recovery = payload["runtime"]["recovery"]
    assert recovery["schema_promotion_allowed"] is True
    assert "unsafe_internal_path" not in json.dumps(recovery)
