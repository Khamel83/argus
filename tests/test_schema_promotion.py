import json
from datetime import datetime, timedelta, timezone


NOW = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)


def _identity():
    from argus.recovery.database import (
        EXPECTED_SCHEMA_HEAD,
        expected_argus_schema_manifest,
        schema_identity_from_manifest,
    )

    return schema_identity_from_manifest(
        expected_argus_schema_manifest(EXPECTED_SCHEMA_HEAD)
    )


def _evidence(*, identity=None, source="a" * 40, image="sha256:" + "b" * 64):
    from argus.recovery.database import restore_identity_sha256

    identity = identity or _identity()
    now = NOW
    evidence = {
        "schema_version": 1,
        "backup": {
            "completed_at": (now - timedelta(hours=2)).isoformat(),
            "databases": ["atlas", "argus"],
            "globals": True,
            "manifest_sha256": "c" * 64,
            "backup_identity": "c" * 64,
            "archive_format": "custom",
            "outside_live_data": True,
            "source_revision": source,
            "image_digest": image,
        },
        "restore": {
            "verified_at": (now - timedelta(hours=1)).isoformat(),
            "databases": ["atlas", "argus"],
            "globals_validated": True,
            "schema_head": identity["schema_head"],
            "backup_manifest_sha256": "c" * 64,
            "source_revision": source,
            "image_digest": image,
            "schema_identity": identity,
            "backup_identity": "c" * 64,
            "restore_identity": None,
            "migration_receipt": "d" * 64,
            "operator_identity": "operator@example",
            "forward_compatible": True,
            "rollback_path_human_approved": True,
            "checks": {
                "schema": True,
                "row_counts": True,
                "integrity": True,
                "argus_read_path": True,
                "migration_compatible": True,
                "metadata_registry_complete": True,
                "schema_contract_clean": True,
                "forward_compatible": True,
                "rollback_path_human_approved": True,
            },
        },
    }
    evidence["restore"]["restore_identity"] = restore_identity_sha256(
        backup_identity=evidence["backup"]["manifest_sha256"],
        schema_identity=identity,
        verified_at=evidence["restore"]["verified_at"],
    )
    return evidence


def test_current_identity_bound_restore_allows_promotion(tmp_path):
    from argus.recovery.evidence import evaluate_recovery_evidence

    path = tmp_path / "recovery.json"
    path.write_text(json.dumps(_evidence()), encoding="utf-8")

    status = evaluate_recovery_evidence(
        path,
        now=NOW,
        candidate={
            "source_revision": "a" * 40,
            "image_digest": "sha256:" + "b" * 64,
            "schema_identity": _identity(),
        },
    )

    assert status["schema_promotion_allowed"] is True
    assert all(status["identity"].values())


def test_missing_identity_fields_block_current_schema_promotion(tmp_path):
    from argus.recovery.evidence import evaluate_promotion_gate

    evidence = _evidence()
    del evidence["restore"]["source_revision"]
    del evidence["restore"]["image_digest"]
    path = tmp_path / "missing-identity.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_promotion_gate(path, schema_change=True, now=NOW)

    assert result["allowed"] is False
    assert "source_identity_mismatch" in result["reasons"]
    assert "image_identity_mismatch" in result["reasons"]


def test_mismatched_candidate_schema_blocks_promotion(tmp_path):
    from argus.recovery.evidence import evaluate_promotion_gate

    path = tmp_path / "mismatched-schema.json"
    path.write_text(json.dumps(_evidence()), encoding="utf-8")
    candidate = _identity()
    candidate["schema_head"] = "0011_future"

    result = evaluate_promotion_gate(
        path,
        schema_change=True,
        now=NOW,
        candidate={"schema_identity": candidate},
    )

    assert result["allowed"] is False
    assert "schema_identity_mismatch" in result["reasons"]


def test_unapproved_rollback_and_forward_compatibility_block(tmp_path):
    from argus.recovery.evidence import evaluate_promotion_gate

    evidence = _evidence()
    evidence["restore"]["forward_compatible"] = False
    evidence["restore"]["rollback_path_human_approved"] = False
    evidence["restore"]["checks"]["forward_compatible"] = False
    evidence["restore"]["checks"]["rollback_path_human_approved"] = False
    path = tmp_path / "unsafe-rollback.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_promotion_gate(path, schema_change=True, now=NOW)

    assert result["allowed"] is False
    assert "restore_not_forward_compatible" in result["reasons"]
    assert "rollback_path_not_human_approved" in result["reasons"]


def test_current_promotion_requires_explicit_registry_and_contract_gates(tmp_path):
    from argus.recovery.evidence import evaluate_promotion_gate

    evidence = _evidence()
    evidence["restore"]["checks"].pop("metadata_registry_complete")
    evidence["restore"]["checks"].pop("schema_contract_clean")
    path = tmp_path / "missing-contract-gates.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_promotion_gate(path, schema_change=True, now=NOW)

    assert result["allowed"] is False
    assert "metadata_registry_incomplete" in result["reasons"]
    assert "schema_contract_dirty" in result["reasons"]


def test_current_promotion_rejects_unbound_restore_identity(tmp_path):
    from argus.recovery.evidence import evaluate_promotion_gate

    evidence = _evidence()
    evidence["restore"]["restore_identity"] = "f" * 64
    path = tmp_path / "unbound-restore-identity.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_promotion_gate(path, schema_change=True, now=NOW)

    assert result["allowed"] is False
    assert "restore_evidence_unverified" in result["reasons"]


def test_current_promotion_rejects_schema_identity_head_mismatch(tmp_path):
    from argus.recovery.evidence import evaluate_promotion_gate

    evidence = _evidence()
    evidence["restore"]["schema_identity"]["schema_head"] = (
        "0009_retrieval_evidence"
    )
    path = tmp_path / "schema-head-mismatch.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_promotion_gate(path, schema_change=True, now=NOW)

    assert result["allowed"] is False
    assert "schema_identity_mismatch" in result["reasons"]
