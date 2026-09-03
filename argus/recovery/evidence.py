"""Sanitized, fail-closed recovery evidence for operators and admin status."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from argus.recovery.database import (
    COMPATIBLE_SCHEMA_HEADS,
    EXPECTED_SCHEMA_HEAD,
    build_schema_identity,
    restore_identity_sha256,
)


BACKUP_MAX_AGE = timedelta(hours=36)
RESTORE_MAX_AGE = timedelta(days=35)
MAX_FUTURE_SKEW = timedelta(minutes=5)
REQUIRED_DATABASES = {"argus", "atlas"}
REQUIRED_RESTORE_CHECKS = {
    "schema",
    "row_counts",
    "integrity",
    "argus_read_path",
    "migration_compatible",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IMAGE_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_OPERATOR_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _age_seconds(when: datetime, now: datetime) -> int:
    return max(0, int((now - when).total_seconds()))


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "state": "unavailable",
        "schema_promotion_allowed": False,
        "reasons": [reason],
        "backup": {"fresh": False},
        "restore": {"fresh": False, "verified": False},
    }


_IDENTITY_FIELDS = (
    "schema_head",
    "migration_chain_sha256",
    "canonical_postgresql_schema_sha256",
    "schema_contract_format",
)


def _coerce_schema_identity(value: object) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    identity = dict(value)
    nested = identity.get("schema_identity")
    if isinstance(nested, Mapping):
        identity = dict(nested)
    if not all(field in identity for field in _IDENTITY_FIELDS):
        return None
    if not all(isinstance(identity[field], str) for field in _IDENTITY_FIELDS):
        return None
    try:
        result = build_schema_identity(
            schema_head=identity["schema_head"],
            migration_chain_sha256=identity["migration_chain_sha256"],
            canonical_postgresql_schema_sha256=identity[
                "canonical_postgresql_schema_sha256"
            ],
            schema_contract_format=identity["schema_contract_format"],
        )
    except (TypeError, ValueError):
        return None
    if "schema" in identity and identity["schema"] != result["schema"]:
        return None
    if "schema_id" in identity and identity["schema_id"] != result["schema_id"]:
        return None
    return result


def _schema_target(value: object) -> tuple[dict[str, str] | None, bool]:
    """Normalize a candidate identity and flag malformed identity input."""
    if not isinstance(value, Mapping):
        return None, value is not None
    if "schema_identity" in value:
        return _coerce_schema_identity(value["schema_identity"]), True
    has_identity_fields = any(
        field in value
        for field in (*_IDENTITY_FIELDS, "schema", "schema_id")
    )
    return (
        _coerce_schema_identity(value) if has_identity_fields else None,
        has_identity_fields,
    )


def _same_schema_identity(
    left: Mapping[str, str] | None,
    right: Mapping[str, str] | None,
) -> bool:
    return left is not None and right is not None and all(
        left.get(field) == right.get(field)
        for field in (*_IDENTITY_FIELDS, "schema_id")
    )


def _all_present_true(*values: object) -> bool:
    present = [value for value in values if value is not None]
    return bool(present) and all(value is True for value in present)


def _identity_evaluation(
    backup: Mapping[str, Any],
    restore: Mapping[str, Any],
    *,
    expected_identity: Mapping[str, Any] | None = None,
    candidate: Mapping[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    restore_identity = _coerce_schema_identity(restore.get("schema_identity"))
    candidate_map = dict(candidate) if isinstance(candidate, Mapping) else {}
    expected_map = (
        dict(expected_identity)
        if isinstance(expected_identity, Mapping)
        else {}
    )
    candidate_schema, candidate_schema_present = _schema_target(candidate)
    expected_schema, expected_schema_present = _schema_target(expected_identity)
    invalid_target_schema = (
        candidate_schema_present and candidate_schema is None
    ) or (expected_schema_present and expected_schema is None)
    target_schema = candidate_schema or expected_schema
    engaged = (
        force
        or restore_identity is not None
        or target_schema is not None
        or invalid_target_schema
    )
    if not engaged:
        return None

    checks = restore.get("checks")
    checks = checks if isinstance(checks, Mapping) else {}
    expected_source = candidate_map.get("source_revision") or expected_map.get(
        "source_revision"
    )
    expected_image = candidate_map.get("image_digest") or expected_map.get(
        "image_digest"
    )
    observed_source = restore.get("source_revision")
    observed_image = restore.get("image_digest")
    source_matches = isinstance(observed_source, str) and bool(
        _COMMIT.fullmatch(observed_source)
    )
    if backup.get("source_revision") is not None:
        source_matches = source_matches and observed_source == backup.get(
            "source_revision"
        )
    if expected_source is not None:
        source_matches = source_matches and observed_source == expected_source
    image_matches = isinstance(observed_image, str) and bool(
        _IMAGE_DIGEST.fullmatch(observed_image)
    )
    if backup.get("image_digest") is not None:
        image_matches = image_matches and observed_image == backup.get("image_digest")
    if expected_image is not None:
        image_matches = image_matches and observed_image == expected_image
    schema_matches = (
        restore_identity is not None
        and not invalid_target_schema
        and restore.get("schema_head") == restore_identity.get("schema_head")
    )
    if candidate_schema is not None:
        schema_matches = schema_matches and _same_schema_identity(
            restore_identity,
            candidate_schema,
        )
    if expected_schema is not None:
        schema_matches = schema_matches and _same_schema_identity(
            restore_identity,
            expected_schema,
        )
    if candidate_schema is not None and expected_schema is not None:
        schema_matches = schema_matches and _same_schema_identity(
            candidate_schema,
            expected_schema,
        )
    metadata_complete = checks.get("metadata_registry_complete") is True
    contract_clean = checks.get("schema_contract_clean") is True
    backup_manifest = backup.get("manifest_sha256")
    restore_identity_value = restore.get("restore_identity")
    restore_identity_matches = (
        isinstance(backup_manifest, str)
        and bool(_SHA256.fullmatch(backup_manifest))
        and isinstance(restore.get("verified_at"), str)
        and isinstance(restore_identity_value, str)
        and bool(_SHA256.fullmatch(restore_identity_value))
        and restore_identity_value
        == restore_identity_sha256(
            backup_identity=backup_manifest,
            schema_identity=restore_identity,
            verified_at=restore["verified_at"],
        )
    )
    restore_verified = (
        restore.get("globals_validated") is True
        and backup.get("backup_identity") == backup_manifest
        and restore.get("backup_manifest_sha256") == backup_manifest
        and restore.get("backup_identity") == backup_manifest
        and isinstance(restore.get("migration_receipt"), str)
        and bool(_SHA256.fullmatch(restore["migration_receipt"]))
        and restore_identity_matches
        and isinstance(restore.get("operator_identity"), str)
        and bool(_OPERATOR_IDENTITY.fullmatch(restore["operator_identity"]))
        and all(
            checks.get(name) is True
            for name in REQUIRED_RESTORE_CHECKS
        )
    )
    forward_compatible = _all_present_true(
        checks.get("forward_compatible"),
        restore.get("forward_compatible"),
        candidate_map.get("forward_compatible"),
    )
    rollback_approved = _all_present_true(
        checks.get("rollback_path_human_approved"),
        restore.get("rollback_path_human_approved"),
        candidate_map.get("rollback_path_human_approved"),
    )
    gate_values = {
        "source_identity_matches": source_matches,
        "image_identity_matches": image_matches,
        "schema_identity_matches": schema_matches,
        "metadata_registry_complete": metadata_complete,
        "schema_contract_clean": contract_clean,
        "restore_evidence_verified": restore_verified,
        "forward_compatible": forward_compatible,
        "rollback_path_human_approved": rollback_approved,
    }
    reason_by_gate = {
        "source_identity_matches": "source_identity_mismatch",
        "image_identity_matches": "image_identity_mismatch",
        "schema_identity_matches": "schema_identity_mismatch",
        "metadata_registry_complete": "metadata_registry_incomplete",
        "schema_contract_clean": "schema_contract_dirty",
        "restore_evidence_verified": "restore_evidence_unverified",
        "forward_compatible": "restore_not_forward_compatible",
        "rollback_path_human_approved": "rollback_path_not_human_approved",
    }
    return {
        "gates": gate_values,
        "reasons": [
            reason_by_gate[name]
            for name, passed in gate_values.items()
            if not passed
        ],
        "schema_identity": restore_identity,
    }


def evaluate_recovery_evidence(
    path: Path | str,
    *,
    now: datetime | None = None,
    backup_max_age: timedelta = BACKUP_MAX_AGE,
    restore_max_age: timedelta = RESTORE_MAX_AGE,
    expected_identity: Mapping[str, Any] | None = None,
    candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read evidence and return only bounded, non-sensitive administrative data."""
    evidence_path = Path(path)
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _unavailable("recovery_evidence_unavailable")
    except (OSError, json.JSONDecodeError):
        return _unavailable("recovery_evidence_invalid")

    try:
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("unsupported evidence schema")
        backup = payload["backup"]
        restore = payload["restore"]
        if not isinstance(backup, dict) or not isinstance(restore, dict):
            raise ValueError("invalid evidence sections")
        observed_at = (now or _utc_now()).astimezone(timezone.utc)
        backup_at = _timestamp(backup["completed_at"])
        restore_at = _timestamp(restore["verified_at"])
        if (
            backup_at > observed_at + MAX_FUTURE_SKEW
            or restore_at > observed_at + MAX_FUTURE_SKEW
        ):
            raise ValueError("evidence timestamp is in the future")
        databases = sorted(
            value
            for value in backup.get("databases", [])
            if isinstance(value, str) and value in REQUIRED_DATABASES
        )
        checks = restore.get("checks")
        if not isinstance(checks, dict):
            raise ValueError("invalid restore checks")
    except (KeyError, TypeError, ValueError):
        return _unavailable("recovery_evidence_invalid")

    reasons: list[str] = []
    backup_fresh = observed_at - backup_at <= backup_max_age
    restore_fresh = observed_at - restore_at <= restore_max_age
    backup_scope_complete = (
        set(databases) == REQUIRED_DATABASES
        and backup.get("globals") is True
        and backup.get("archive_format") == "custom"
        and backup.get("outside_live_data") is True
    )
    restore_databases = sorted(
        value
        for value in restore.get("databases", [])
        if isinstance(value, str) and value in REQUIRED_DATABASES
    )
    restore_scope_complete = (
        set(restore_databases) == REQUIRED_DATABASES
        and restore.get("globals_validated") is True
    )
    restore_verified = (
        restore_scope_complete
        and restore.get("schema_head") in COMPATIBLE_SCHEMA_HEADS
        and restore.get("backup_manifest_sha256") == backup.get("manifest_sha256")
        and all(checks.get(name) is True for name in REQUIRED_RESTORE_CHECKS)
    )

    if not backup_fresh:
        reasons.append("backup_stale")
    if not backup_scope_complete:
        reasons.append("backup_scope_incomplete")
    if not restore_fresh:
        reasons.append("restore_stale")
    if not restore_verified:
        reasons.append("restore_verification_failed")

    identity = _identity_evaluation(
        backup,
        restore,
        expected_identity=expected_identity,
        candidate=candidate,
        force=restore.get("schema_head") == EXPECTED_SCHEMA_HEAD,
    )
    if identity is not None:
        reasons.extend(identity["reasons"])

    allowed = not reasons
    result: dict[str, Any] = {
        "state": "ready" if allowed else "degraded",
        "schema_promotion_allowed": allowed,
        "reasons": reasons,
        "backup": {
            "completed_at": backup_at.isoformat(),
            "age_seconds": _age_seconds(backup_at, observed_at),
            "fresh": backup_fresh,
            "databases": databases,
            "globals": backup.get("globals") is True,
            "scope_complete": backup_scope_complete,
        },
        "restore": {
            "verified_at": restore_at.isoformat(),
            "age_seconds": _age_seconds(restore_at, observed_at),
            "fresh": restore_fresh,
            "verified": restore_verified,
            "databases": restore_databases,
            "globals_validated": restore.get("globals_validated") is True,
            "schema_head": (
                restore.get("schema_head")
                if isinstance(restore.get("schema_head"), str)
                else None
            ),
        },
    }
    if identity is not None:
        result["identity"] = {
            **identity["gates"],
            **(
                identity["schema_identity"]
                if identity["schema_identity"] is not None
                else {
                    "schema": "argus.schema-identity.v1",
                    "schema_head": None,
                    "migration_chain_sha256": None,
                    "canonical_postgresql_schema_sha256": None,
                    "schema_contract_format": None,
                    "schema_id": None,
                }
            ),
        }
    return result


def evaluate_promotion_gate(
    path: Path | str,
    *,
    schema_change: bool,
    now: datetime | None = None,
    expected_identity: Mapping[str, Any] | None = None,
    candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the recovery gate; schema changes fail closed, code-only reports drift."""
    evidence = evaluate_recovery_evidence(
        path,
        now=now,
        expected_identity=expected_identity,
        candidate=candidate,
    )
    reasons = list(evidence["reasons"])
    allowed = not schema_change or evidence["schema_promotion_allowed"]
    return {
        "allowed": allowed,
        "state": (
            "ready" if allowed and not reasons else "degraded" if allowed else "blocked"
        ),
        "schema_change": schema_change,
        "reasons": reasons,
    }


def recovery_status_from_environment() -> dict[str, Any]:
    """Read the configured read-only evidence artifact for admin status."""
    path = os.environ.get("ARGUS_RECOVERY_EVIDENCE_PATH", "").strip()
    if not path:
        return _unavailable("recovery_evidence_not_configured")
    return evaluate_recovery_evidence(path)


def lifecycle_recovery_status_from_environment(
    *,
    stop_event: threading.Event,
    timeout_seconds: float = 2.0,
) -> dict[str, Any]:
    """Read recovery evidence behind a terminable process boundary."""
    path = os.environ.get("ARGUS_RECOVERY_EVIDENCE_PATH", "").strip()
    if not path:
        return _unavailable("recovery_evidence_not_configured")
    if stop_event.is_set():
        return _unavailable("recovery_evidence_unavailable")
    process = subprocess.Popen(
        [sys.executable, "-m", __name__, "--lifecycle-status", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    while process.poll() is None:
        if stop_event.is_set() or time.monotonic() >= deadline:
            reaped = _terminate_and_reap_bounded(process)
            return _unavailable(
                "recovery_evidence_unavailable"
                if reaped
                else "recovery_helper_unreaped"
            )
        stop_event.wait(0.02)
    stdout, _ = process.communicate()
    if process.returncode != 0:
        return _unavailable("recovery_evidence_unavailable")
    try:
        status = json.loads(stdout)
    except (TypeError, json.JSONDecodeError):
        return _unavailable("recovery_evidence_invalid")
    return (
        status
        if isinstance(status, dict)
        else _unavailable("recovery_evidence_invalid")
    )


def _terminate_and_reap_bounded(
    process: subprocess.Popen,
    *,
    grace_seconds: float = 0.5,
) -> bool:
    """Best-effort reap without ever making shutdown wait unboundedly."""
    process.terminate()
    deadline = time.monotonic() + max(0.01, grace_seconds)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return True
        time.sleep(0.01)
    process.kill()
    deadline = time.monotonic() + max(0.01, grace_seconds)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return True
        time.sleep(0.01)
    return process.poll() is not None


def _lifecycle_status_main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--lifecycle-status":
        return 2
    print(json.dumps(evaluate_recovery_evidence(sys.argv[2])))
    return 0


if __name__ == "__main__":
    raise SystemExit(_lifecycle_status_main())
