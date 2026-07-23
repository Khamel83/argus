"""Checksum-bound backup-set manifests and source database inventories."""

from __future__ import annotations

import hashlib
import json
import re
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
    inventory_collector=None,
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
    if inventory_collector is None:
        from argus.recovery.database import collect_source_inventory

        inventory_collector = collect_source_inventory
    root_payload = json.loads(
        (resolved_root / BACKUP_ROOT_MARKER).read_text(encoding="utf-8")
    )
    parsed = datetime.strptime(completed_at, "%Y%m%dT%H%M%SZ")
    payload = {
        "schema_version": 1,
        "root_id": root_payload["root_id"],
        "completed_at": parsed.isoformat() + "+00:00",
        "databases": {
            name: _inventory(inventory_collector(name), name)
            for name in ("atlas", "argus")
        },
        "files": dict(sorted(files.items())),
        "globals_without_passwords": True,
        "archive_format": "custom",
    }
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
        or payload.get("schema_version") != 1
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
    return {
        "path": candidate.resolve(strict=True),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "completed_at": completed_at,
        "databases": {
            name: _inventory(databases[name], name)
            for name in ("atlas", "argus")
        },
    }
