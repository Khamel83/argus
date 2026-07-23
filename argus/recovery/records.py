"""Atomic recovery evidence records and bounded backup retention."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.recovery.operator import retained_snapshot_names


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SNAPSHOT = re.compile(r"^\d{8}T\d{6}Z$")
_RESTORE_CHECKS = (
    "schema",
    "row_counts",
    "integrity",
    "argus_read_path",
    "migration_compatible",
)


def _backup_record(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "completed_at",
        "databases",
        "globals",
        "manifest_sha256",
        "archive_format",
        "outside_live_data",
    }
    return {key: value[key] for key in allowed if key in value}


def _restore_record(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    checks = value.get("checks")
    if not isinstance(checks, dict):
        return None
    return {
        "verified_at": value.get("verified_at"),
        "databases": ["atlas", "argus"],
        "globals_validated": value.get("globals_validated") is True,
        "schema_head": value.get("schema_head"),
        "checks": {name: checks.get(name) is True for name in _RESTORE_CHECKS},
    }


def _existing(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1}
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("existing recovery evidence is unreadable") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("existing recovery evidence has an invalid schema")
    result: dict[str, Any] = {"schema_version": 1}
    backup = _backup_record(payload.get("backup"))
    restore = _restore_record(payload.get("restore"))
    if backup is not None:
        result["backup"] = backup
    if restore is not None:
        result["restore"] = restore
    return result


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def record_backup(
    path: Path | str,
    *,
    completed_at: str,
    manifest_sha256: str,
) -> None:
    """Atomically record a structurally verified shared backup set."""
    if _SNAPSHOT.fullmatch(completed_at) is None:
        raise ValueError("completed_at must use YYYYmmddTHHMMSSZ")
    if _SHA256.fullmatch(manifest_sha256) is None:
        raise ValueError("manifest_sha256 must be a lowercase SHA-256")
    parsed = datetime.strptime(completed_at, "%Y%m%dT%H%M%SZ").replace(
        tzinfo=timezone.utc
    )
    evidence_path = Path(path)
    payload = _existing(evidence_path)
    payload["backup"] = {
        "completed_at": parsed.isoformat(),
        "databases": ["atlas", "argus"],
        "globals": True,
        "manifest_sha256": manifest_sha256,
        "archive_format": "custom",
        "outside_live_data": True,
    }
    _atomic_write(evidence_path, payload)


def record_restore(
    path: Path | str,
    *,
    schema_head: str,
    verified_at: datetime | None = None,
) -> None:
    """Record successful checks; callers invoke this only after every verifier exits zero."""
    if not schema_head or len(schema_head) > 128:
        raise ValueError("schema_head is invalid")
    timestamp = (verified_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    evidence_path = Path(path)
    payload = _existing(evidence_path)
    payload["restore"] = {
        "verified_at": timestamp.isoformat(),
        "databases": ["atlas", "argus"],
        "globals_validated": True,
        "schema_head": schema_head.strip(),
        "checks": {name: True for name in _RESTORE_CHECKS},
    }
    _atomic_write(evidence_path, payload)


def prune_snapshots(
    root: Path | str,
    *,
    apply: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Prune only timestamp-named child directories under an explicit backup root."""
    root_path = Path(root)
    if not root_path.is_absolute():
        raise ValueError("backup root must be absolute")
    resolved = root_path.resolve()
    if resolved == Path("/") or len(resolved.parts) < 3 or not resolved.is_dir():
        raise ValueError("backup root is unsafe or unavailable")

    names = sorted(
        child.name
        for child in resolved.iterdir()
        if child.is_dir() and _SNAPSHOT.fullmatch(child.name)
    )
    kept = retained_snapshot_names(names, now=now)
    removed = sorted(set(names) - kept)
    if apply:
        for name in removed:
            target = resolved / name
            if target.parent != resolved or _SNAPSHOT.fullmatch(target.name) is None:
                raise ValueError("refusing unsafe retention target")
            shutil.rmtree(target)
    return {
        "applied": apply,
        "kept": sorted(kept),
        "removed": removed,
    }
