"""Sanitized, fail-closed recovery evidence for operators and admin status."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from argus.recovery.database import EXPECTED_SCHEMA_HEAD


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


def evaluate_recovery_evidence(
    path: Path | str,
    *,
    now: datetime | None = None,
    backup_max_age: timedelta = BACKUP_MAX_AGE,
    restore_max_age: timedelta = RESTORE_MAX_AGE,
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
        and restore.get("schema_head") == EXPECTED_SCHEMA_HEAD
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

    allowed = not reasons
    return {
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


def evaluate_promotion_gate(
    path: Path | str,
    *,
    schema_change: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply the recovery gate; schema changes fail closed, code-only reports drift."""
    evidence = evaluate_recovery_evidence(path, now=now)
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
