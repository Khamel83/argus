"""Checksum-bound backup-set manifests and source database inventories."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

from argus.recovery.operator import (
    BACKUP_ROOT_MARKER,
    validate_backup_root,
)


BACKUP_SET_MANIFEST = ".argus-backup-set.json"
REQUIRED_BACKUP_FILES = {
    "atlas.dump",
    "argus.dump",
    "globals.sql",
    "SHA256SUMS",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SNAPSHOT = re.compile(r"^\d{8}T\d{6}Z$")
SNAPSHOT_SIDECAR = ".{tenant}-snapshot-inventory.json"
SNAPSHOT_INVENTORY_SCHEMA = "argus.postgres-snapshot-inventory/v1"
SNAPSHOT_METHOD = "pg-exported-snapshot-v1"
MAX_SNAPSHOT_SIDECAR_BYTES = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup_manifest(
    stage: Path | str,
    *,
    root: Path | str,
    live_data: Path | str,
    completed_at: str,
) -> dict[str, Any]:
    """Create a source-derived manifest after dump/list/checksum commands succeed."""
    resolved_root = validate_backup_root(root, live_data=live_data)
    stage_path = Path(stage)
    if (
        not stage_path.is_absolute()
        or stage_path.is_symlink()
        or not stage_path.name.startswith(".staging.")
        or stage_path.parent.resolve(strict=True) != resolved_root
        or not stage_path.is_dir()
        or _SNAPSHOT.fullmatch(completed_at) is None
    ):
        raise ValueError("backup staging directory is invalid")
    files = {}
    for filename in REQUIRED_BACKUP_FILES:
        target = stage_path / filename
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"required backup artifact is missing: {filename}")
        files[filename] = sha256_file(target)
    globals_text = (stage_path / "globals.sql").read_text(
        encoding="utf-8",
        errors="replace",
    )
    if re.search(r"SCRAM-SHA-256|md5[0-9a-f]{32}", globals_text, re.IGNORECASE):
        raise ValueError("cluster globals contain a credential verifier")
    inventories: dict[str, dict[str, Any]] = {}
    provenance: dict[str, dict[str, str]] = {}
    for name in ("atlas", "argus"):
        sidecar = stage_path / SNAPSHOT_SIDECAR.format(tenant=name)
        try:
            metadata = sidecar.lstat()
        except OSError as error:
            raise ValueError(f"{name} snapshot inventory is unavailable") from error
        if (
            not stat.S_ISREG(metadata.st_mode)
            or sidecar.is_symlink()
            or metadata.st_size > MAX_SNAPSHOT_SIDECAR_BYTES
        ):
            raise ValueError(f"{name} snapshot inventory is unavailable")
        try:
            with sidecar.open("rb") as handle:
                sidecar_bytes = handle.read(MAX_SNAPSHOT_SIDECAR_BYTES + 1)
            if len(sidecar_bytes) > MAX_SNAPSHOT_SIDECAR_BYTES:
                raise ValueError(f"{name} snapshot inventory is unavailable")
            record = json.loads(sidecar_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{name} snapshot inventory is invalid") from error
        archive_sha256 = files[f"{name}.dump"]
        if (
            not isinstance(record, dict)
            or record.get("schema") != SNAPSHOT_INVENTORY_SCHEMA
            or record.get("tenant") != name
            or record.get("method") != SNAPSHOT_METHOD
            or record.get("archive_sha256") != archive_sha256
        ):
            raise ValueError(f"{name} snapshot inventory binding is invalid")
        inventories[name] = _inventory(record.get("inventory"), name)
        provenance[name] = {
            "schema": SNAPSHOT_INVENTORY_SCHEMA,
            "tenant": name,
            "method": SNAPSHOT_METHOD,
            "archive_sha256": archive_sha256,
        }
    root_payload = json.loads(
        (resolved_root / BACKUP_ROOT_MARKER).read_text(encoding="utf-8")
    )
    parsed = datetime.strptime(completed_at, "%Y%m%dT%H%M%SZ")
    payload = {
        "schema_version": 2,
        "root_id": root_payload["root_id"],
        "completed_at": parsed.isoformat() + "+00:00",
        "databases": {
            name: {**inventories[name], "snapshot_provenance": provenance[name]}
            for name in ("atlas", "argus")
        },
        "files": dict(sorted(files.items())),
        "globals_without_passwords": True,
        "archive_format": "custom",
    }
    for name in ("atlas", "argus"):
        (stage_path / SNAPSHOT_SIDECAR.format(tenant=name)).unlink()
    manifest = stage_path / BACKUP_SET_MANIFEST
    with manifest.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")
    manifest.chmod(0o600)
    return payload


def _inventory(value: object, database: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{database} source inventory is invalid")
    tables = value.get("tables")
    if (
        not isinstance(tables, dict)
        or not tables
        or not all(
            isinstance(name, str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            and isinstance(count, int)
            and count >= 0
            for name, count in tables.items()
        )
        or not _SHA256.fullmatch(str(value.get("schema_sha256", "")))
        or value.get("constraints_validated") is not True
    ):
        raise ValueError(f"{database} source inventory is invalid")
    if database == "argus":
        missing = {"retrieval_requests", "retrieval_runs"} - set(tables)
        if missing:
            raise ValueError("argus source inventory is missing required tables")
    return {
        "tables": dict(sorted(tables.items())),
        "schema_sha256": value["schema_sha256"],
        "constraints_validated": True,
    }


def load_verified_backup_set(
    backup_set: Path | str,
    *,
    root: Path | str,
    live_data: Path | str,
) -> dict[str, Any]:
    """Recompute a backup set's cryptographic bindings under its owned root."""
    resolved_root = validate_backup_root(root, live_data=live_data)
    candidate = Path(backup_set)
    if (
        not candidate.is_absolute()
        or candidate.is_symlink()
        or _SNAPSHOT.fullmatch(candidate.name) is None
        or candidate.parent.resolve(strict=True) != resolved_root
        or candidate.resolve(strict=True).parent != resolved_root
    ):
        raise ValueError("backup set must be a direct timestamped directory")
    manifest_path = candidate / BACKUP_SET_MANIFEST
    if manifest_path.is_symlink():
        raise ValueError("backup manifest cannot be a symlink")
    try:
        manifest_bytes = manifest_path.read_bytes()
        payload = json.loads(manifest_bytes)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("backup manifest is unavailable or invalid") from error
    root_payload = json.loads(
        (resolved_root / BACKUP_ROOT_MARKER).read_text(encoding="utf-8")
    )
    try:
        completed_at = datetime.fromisoformat(
            str(payload["completed_at"]).replace("Z", "+00:00")
        )
        files = payload["files"]
        databases = payload["databases"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("backup manifest is invalid") from error
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") not in {1, 2}
        or payload.get("root_id") != root_payload["root_id"]
        or completed_at.tzinfo is None
        or completed_at.strftime("%Y%m%dT%H%M%SZ") != candidate.name
        or payload.get("globals_without_passwords") is not True
        or payload.get("archive_format") != "custom"
        or not isinstance(files, dict)
        or set(files) != REQUIRED_BACKUP_FILES
        or not isinstance(databases, dict)
        or set(databases) != {"atlas", "argus"}
    ):
        raise ValueError("backup manifest is invalid")
    for filename in REQUIRED_BACKUP_FILES:
        expected = files.get(filename)
        target = candidate / filename
        if (
            not isinstance(expected, str)
            or _SHA256.fullmatch(expected) is None
            or target.is_symlink()
            or not target.is_file()
            or sha256_file(target) != expected
        ):
            raise ValueError(f"backup file checksum mismatch: {filename}")
    version = payload["schema_version"]
    snapshot_provenance: dict[str, dict[str, str]] = {}
    if version == 2:
        for name in ("atlas", "argus"):
            if not isinstance(databases[name], dict):
                raise ValueError(f"{name} source inventory is invalid")
            provenance = databases[name].get("snapshot_provenance")
            expected = {
                "schema": SNAPSHOT_INVENTORY_SCHEMA,
                "tenant": name,
                "method": SNAPSHOT_METHOD,
                "archive_sha256": files[f"{name}.dump"],
            }
            if provenance != expected:
                raise ValueError(f"{name} snapshot provenance is invalid")
            snapshot_provenance[name] = expected
    return {
        "path": candidate.resolve(strict=True),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "completed_at": completed_at,
        "databases": {
            name: _inventory(databases[name], name)
            for name in ("atlas", "argus")
        },
        "schema_version": version,
        "snapshot_consistency_proven": version == 2,
        "snapshot_provenance": snapshot_provenance,
    }
