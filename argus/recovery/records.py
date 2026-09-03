"""Atomic recovery evidence records and bounded backup retention."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from argus.recovery.artifacts import load_verified_backup_set
from argus.recovery.operator import (
    BACKUP_ROOT_MARKER,
    retained_snapshot_names,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IMAGE_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_SAFE_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$")
_SNAPSHOT = re.compile(r"^\d{8}T\d{6}Z$")
_SNAPSHOT_MARKER = ".argus-backup-set.json"
_MAX_RETENTION_ENTRIES = 4096
_RESTORE_CHECKS = (
    "schema",
    "row_counts",
    "integrity",
    "argus_read_path",
    "migration_compatible",
)
_IDENTITY_CHECKS = (
    "metadata_registry_complete",
    "schema_contract_clean",
    "forward_compatible",
    "rollback_path_human_approved",
)


def _normalize_schema_identity(
    value: Mapping[str, Any],
    *,
    schema_head: str | None = None,
) -> dict[str, str]:
    """Accept the four-field tuple and return its canonical identity object."""
    identity_fields = (
        "schema_head",
        "migration_chain_sha256",
        "canonical_postgresql_schema_sha256",
        "schema_contract_format",
    )
    identity = dict(value)
    allowed = set(identity_fields) | {"schema", "schema_id"}
    if (
        set(identity) - allowed
        or not all(field in identity for field in identity_fields)
    ):
        raise ValueError("schema_identity is invalid")
    from argus.recovery.database import build_schema_identity

    try:
        canonical = build_schema_identity(
            schema_head=identity["schema_head"],
            migration_chain_sha256=identity["migration_chain_sha256"],
            canonical_postgresql_schema_sha256=identity[
                "canonical_postgresql_schema_sha256"
            ],
            schema_contract_format=identity["schema_contract_format"],
        )
    except (TypeError, ValueError) as error:
        raise ValueError("schema_identity is invalid") from error
    if "schema" in identity and identity["schema"] != canonical["schema"]:
        raise ValueError("schema_identity is invalid")
    if "schema_id" in identity and identity["schema_id"] != canonical["schema_id"]:
        raise ValueError("schema_identity schema_id is invalid")
    if schema_head is not None and canonical["schema_head"] != schema_head.strip():
        raise ValueError("schema_identity does not match schema_head")
    return canonical


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
        "backup_identity",
        "source_revision",
        "image_digest",
        "operator_identity",
    }
    return {key: value[key] for key in allowed if key in value}


def _restore_record(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    checks = value.get("checks")
    if not isinstance(checks, dict):
        return None
    result: dict[str, Any] = {
        "verified_at": value.get("verified_at"),
        "databases": ["atlas", "argus"],
        "globals_validated": value.get("globals_validated") is True,
        "schema_head": value.get("schema_head"),
        "backup_manifest_sha256": value.get("backup_manifest_sha256"),
        "checks": {
            name: checks.get(name) is True
            for name in (*_RESTORE_CHECKS, *_IDENTITY_CHECKS)
            if name in checks
        },
    }
    optional = (
        "source_revision",
        "image_digest",
        "schema_identity",
        "migration_receipt",
        "backup_identity",
        "restore_identity",
        "operator_identity",
        "forward_compatible",
        "rollback_path_human_approved",
    )
    result.update({key: value[key] for key in optional if key in value})
    return result


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


@contextmanager
def _evidence_lock(path: Path):
    """Serialize evidence verification and replacement for one record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path.parent / f"{path.name}.lock",
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _record_backup(
    path: Path | str,
    *,
    completed_at: str,
    manifest_sha256: str,
    source_revision: str | None = None,
    image_digest: str | None = None,
    operator_identity: str | None = None,
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
    if source_revision is not None and _COMMIT.fullmatch(source_revision) is None:
        raise ValueError("source_revision must be a bounded source identity")
    if image_digest is not None and _IMAGE_DIGEST.fullmatch(image_digest) is None:
        raise ValueError("image_digest must be an immutable SHA-256 reference")
    if operator_identity is not None and (
        not isinstance(operator_identity, str)
        or _SAFE_IDENTITY.fullmatch(operator_identity) is None
    ):
        raise ValueError("operator_identity is invalid")
    backup: dict[str, Any] = {
        "completed_at": parsed.isoformat(),
        "databases": ["atlas", "argus"],
        "globals": True,
        "manifest_sha256": manifest_sha256,
        "archive_format": "custom",
        "outside_live_data": True,
        "backup_identity": manifest_sha256,
    }
    backup.update(
        {
            key: value
            for key, value in {
                "source_revision": source_revision,
                "image_digest": image_digest,
                "operator_identity": operator_identity,
            }.items()
            if value is not None
        }
    )
    payload["backup"] = backup
    _atomic_write(evidence_path, payload)


def _record_restore(
    path: Path | str,
    *,
    schema_head: str,
    expected_manifest_sha256: str,
    verified_at: datetime | None = None,
    source_revision: str | None = None,
    image_digest: str | None = None,
    schema_identity: Mapping[str, Any] | None = None,
    migration_receipt: str | None = None,
    operator_identity: str | None = None,
    metadata_registry_complete: bool | None = None,
    schema_contract_clean: bool | None = None,
    forward_compatible: bool | None = None,
    rollback_path_human_approved: bool | None = None,
) -> None:
    """Record successful checks; callers invoke this only after every verifier exits zero."""
    if not schema_head or len(schema_head) > 128:
        raise ValueError("schema_head is invalid")
    if source_revision is not None and _COMMIT.fullmatch(source_revision) is None:
        raise ValueError("source_revision must be a bounded source identity")
    if image_digest is not None and _IMAGE_DIGEST.fullmatch(image_digest) is None:
        raise ValueError("image_digest must be an immutable SHA-256 reference")
    if migration_receipt is not None and _SHA256.fullmatch(migration_receipt) is None:
        raise ValueError("migration_receipt must be a lowercase SHA-256")
    if operator_identity is not None and (
        not isinstance(operator_identity, str)
        or _SAFE_IDENTITY.fullmatch(operator_identity) is None
    ):
        raise ValueError("operator_identity is invalid")
    for name, value in (
        ("metadata_registry_complete", metadata_registry_complete),
        ("schema_contract_clean", schema_contract_clean),
        ("forward_compatible", forward_compatible),
        ("rollback_path_human_approved", rollback_path_human_approved),
    ):
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
    if schema_identity is not None:
        if not isinstance(schema_identity, Mapping):
            raise ValueError("schema_identity must be an object")
        schema_identity = _normalize_schema_identity(
            schema_identity,
            schema_head=schema_head,
        )
    timestamp = (verified_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    evidence_path = Path(path)
    payload = _existing(evidence_path)
    backup = payload.get("backup")
    if (
        not isinstance(backup, dict)
        or backup.get("manifest_sha256") != expected_manifest_sha256
    ):
        raise ValueError("backup evidence changed during restore verification")
    restore: dict[str, Any] = {
        "verified_at": timestamp.isoformat(),
        "databases": ["atlas", "argus"],
        "globals_validated": True,
        "schema_head": schema_head.strip(),
        "backup_manifest_sha256": expected_manifest_sha256,
        "checks": {name: True for name in _RESTORE_CHECKS},
    }
    optional = {
        "source_revision": source_revision,
        "image_digest": image_digest,
        "schema_identity": schema_identity,
        "migration_receipt": migration_receipt,
        "backup_identity": expected_manifest_sha256,
        "operator_identity": operator_identity,
        "metadata_registry_complete": metadata_registry_complete,
        "schema_contract_clean": schema_contract_clean,
        "forward_compatible": forward_compatible,
        "rollback_path_human_approved": rollback_path_human_approved,
    }
    restore.update({key: value for key, value in optional.items() if value is not None})
    for name, value in (
        ("metadata_registry_complete", metadata_registry_complete),
        ("schema_contract_clean", schema_contract_clean),
        ("forward_compatible", forward_compatible),
        ("rollback_path_human_approved", rollback_path_human_approved),
    ):
        if value is not None:
            restore["checks"][name] = value
    from argus.recovery.database import restore_identity_sha256

    restore["restore_identity"] = restore_identity_sha256(
        backup_identity=expected_manifest_sha256,
        schema_identity=schema_identity,
        verified_at=restore["verified_at"],
    )
    payload["restore"] = restore
    _atomic_write(evidence_path, payload)


def record_verified_backup(
    path: Path | str,
    *,
    backup_set: Path | str,
    root: Path | str,
    live_data: Path | str,
    source_revision: str | None = None,
    image_digest: str | None = None,
    operator_identity: str | None = None,
) -> None:
    """Record evidence derived from a checksum-verified owned backup set."""
    verified = load_verified_backup_set(
        backup_set,
        root=root,
        live_data=live_data,
    )
    evidence_path = Path(path)
    with _evidence_lock(evidence_path):
        existing = _existing(evidence_path)
        prior = existing.get("backup")
        if isinstance(prior, dict) and prior.get("completed_at"):
            prior_at = datetime.fromisoformat(str(prior["completed_at"]))
            if verified["completed_at"] < prior_at:
                raise ValueError("refusing older backup evidence replay")
            if (
                verified["completed_at"] == prior_at
                and prior.get("manifest_sha256") != verified["manifest_sha256"]
            ):
                raise ValueError("refusing changed backup manifest for same timestamp")
        _record_backup(
            evidence_path,
            completed_at=verified["completed_at"].strftime("%Y%m%dT%H%M%SZ"),
            manifest_sha256=verified["manifest_sha256"],
            source_revision=source_revision,
            image_digest=image_digest,
            operator_identity=operator_identity,
        )


def record_verified_restore(
    path: Path | str,
    *,
    backup_set: Path | str,
    root: Path | str,
    live_data: Path | str,
    argus_database: str,
    atlas_database: str,
    verified_at: datetime | None = None,
    verify_source=None,
    migrate_argus=None,
    verify_argus=None,
    verify_atlas=None,
    source_revision: str | None = None,
    image_digest: str | None = None,
    schema_identity: Mapping[str, Any] | None = None,
    migration_receipt: str | None = None,
    operator_identity: str | None = None,
    metadata_registry_complete: bool | None = None,
    schema_contract_clean: bool | None = None,
    forward_compatible: bool | None = None,
    rollback_path_human_approved: bool | None = None,
) -> None:
    """Verify restored databases against their source manifest before evidence."""
    from argus.recovery.database import (
        EXPECTED_SCHEMA_HEAD,
        expected_argus_schema_manifest,
        verify_argus_database,
        verify_atlas_database,
        verify_restored_source_inventory,
    )
    from argus.recovery.operator import validate_scratch_database

    verified = load_verified_backup_set(
        backup_set,
        root=root,
        live_data=live_data,
    )
    evidence_path = Path(path)
    expected_manifest_sha256 = verified["manifest_sha256"]
    with _evidence_lock(evidence_path):
        existing = _existing(evidence_path)
        backup = existing.get("backup")
        if (
            not isinstance(backup, dict)
            or backup.get("manifest_sha256") != expected_manifest_sha256
        ):
            raise ValueError("restore proof is not bound to current backup evidence")
        if schema_identity is not None and not isinstance(schema_identity, Mapping):
            raise ValueError("schema_identity must be an object")
        argus_name = validate_scratch_database(argus_database)
        atlas_name = validate_scratch_database(atlas_database, tenant="atlas")
        source_verifier = verify_source or (
            lambda database, tenant, expected: verify_restored_source_inventory(
                database,
                tenant=tenant,
                expected_inventory=expected,
            )
        )
        source_verifier(
            argus_name,
            "argus",
            verified["databases"]["argus"],
        )
        source_verifier(
            atlas_name,
            "atlas",
            verified["databases"]["atlas"],
        )
        if migrate_argus is None:
            from alembic import command
            from alembic.config import Config

            def migrate_argus(database):
                repository_root = Path(__file__).parents[2]
                config = Config(str(repository_root / "alembic.ini"))
                config.set_main_option(
                    "script_location",
                    str(repository_root / "migrations"),
                )
                config.set_main_option(
                    "sqlalchemy.url",
                    f"postgresql+psycopg2:///{database}",
                )
                command.upgrade(config, "head")
        migrate_argus(argus_name)
        argus_verifier = verify_argus or (
            lambda database, expected: verify_argus_database(
                database,
                expected_schema_manifest=expected,
            )
        )
        atlas_verifier = verify_atlas or (
            lambda database, expected: verify_atlas_database(
                database,
                expected_inventory=expected,
            )
        )
        argus_report = argus_verifier(
            argus_name,
            expected_argus_schema_manifest(EXPECTED_SCHEMA_HEAD),
        )
        atlas_report = atlas_verifier(
            atlas_name,
            verified["databases"]["atlas"],
        )
        required_argus = set(_RESTORE_CHECKS)
        if (
            not all(
                argus_report.get("checks", {}).get(name) is True
                for name in required_argus
            )
            or not all(
                atlas_report.get("checks", {}).get(name) is True
                for name in ("schema", "row_counts", "integrity")
            )
        ):
            raise ValueError("restore verification did not pass every required check")
        current = load_verified_backup_set(
            backup_set,
            root=root,
            live_data=live_data,
        )
        if current["manifest_sha256"] != expected_manifest_sha256:
            raise ValueError("backup manifest changed during restore verification")
        report_schema_identity = argus_report.get("schema_identity")
        if schema_identity is not None:
            schema_identity = _normalize_schema_identity(
                schema_identity,
                schema_head=str(argus_report["schema_head"]),
            )
            if isinstance(report_schema_identity, Mapping) and _normalize_schema_identity(
                report_schema_identity,
                schema_head=str(argus_report["schema_head"]),
            ) != schema_identity:
                raise ValueError("schema_identity does not match verifier report")
        elif isinstance(report_schema_identity, Mapping):
            schema_identity = _normalize_schema_identity(
                report_schema_identity,
                schema_head=str(argus_report["schema_head"]),
            )
        restore_source_revision = (
            source_revision
            if source_revision is not None
            else argus_report.get("source_revision")
            or backup.get("source_revision")
        )
        restore_image_digest = (
            image_digest
            if image_digest is not None
            else argus_report.get("image_digest")
            or backup.get("image_digest")
        )
        restore_operator_identity = operator_identity or backup.get(
            "operator_identity"
        )
        report_checks = argus_report.get("checks")
        report_checks = report_checks if isinstance(report_checks, Mapping) else {}
        _record_restore(
            evidence_path,
            schema_head=str(argus_report["schema_head"]),
            expected_manifest_sha256=expected_manifest_sha256,
            verified_at=verified_at,
            source_revision=restore_source_revision,
            image_digest=restore_image_digest,
            schema_identity=(
                schema_identity
                if schema_identity is not None
                else None
            ),
            migration_receipt=(
                migration_receipt
                if migration_receipt is not None
                else argus_report.get("migration_receipt")
            ),
            operator_identity=restore_operator_identity,
            metadata_registry_complete=(
                metadata_registry_complete
                if metadata_registry_complete is not None
                else report_checks.get("metadata_registry_complete")
            ),
            schema_contract_clean=(
                schema_contract_clean
                if schema_contract_clean is not None
                else report_checks.get("schema_contract_clean")
            ),
            forward_compatible=(
                forward_compatible
                if forward_compatible is not None
                else argus_report.get("forward_compatible")
            ),
            rollback_path_human_approved=(
                rollback_path_human_approved
                if rollback_path_human_approved is not None
                else argus_report.get("rollback_path_human_approved")
            ),
        )


def plan_snapshot_retention(
    root: Path | str,
    *,
    live_data: Path | str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a bounded, sanitized retention plan without mutating the filesystem."""
    root_path = Path(root)
    live_path = Path(live_data)
    if not root_path.is_absolute() or not live_path.is_absolute():
        raise ValueError("backup root and live data directory must be absolute")
    root_fd = _open_directory_no_follow(root_path, label="backup root")
    root_marker_fd = -1
    root_marker_signature = ""
    held_snapshots: dict[
        str,
        tuple[int, os.stat_result, str, str],
    ] = {}
    try:
        root_metadata = os.fstat(root_fd)
        _assert_path_matches_descriptor(root_path, root_metadata, label="backup root")
        if root_metadata.st_uid != os.geteuid() or root_metadata.st_mode & 0o022:
            raise ValueError(
                "backup root must be operator-owned and not group/world writable"
            )
        resolved = root_path.resolve(strict=True)
        # The live directory is a boundary check only; retention never reads
        # its contents.  stat(..., follow_symlinks=False) lets an operator
        # validate a root-owned/container-owned PGDATA path without requiring
        # read permission or changing its ownership.  Snapshot and marker
        # files still use descriptor-bound O_NOATIME reads below.
        live_metadata = _stat_real_directory_no_follow(
            live_path,
            label="live data directory",
        )
        resolved_live = live_path.resolve(strict=True)
        if (
            resolved == Path("/")
            or len(resolved.parts) < 3
            or resolved == resolved_live
            or resolved in resolved_live.parents
            or resolved_live in resolved.parents
        ):
            raise ValueError("backup root must be canonically outside live data")
        root_marker_fd = _open_regular_noatime_at(
            root_fd,
            BACKUP_ROOT_MARKER,
            label="backup root ownership marker",
        )
        fcntl.flock(root_marker_fd, fcntl.LOCK_SH)
        root_payload = _read_json_descriptor(root_marker_fd)
        root_marker_signature = _regular_descriptor_signature(
            root_marker_fd,
            expected_device=root_metadata.st_dev,
        )
        if (
            root_payload.get("schema_version") != 1
            or not isinstance(root_payload.get("root_id"), str)
            or re.fullmatch(r"[0-9a-f]{32}", root_payload["root_id"]) is None
            or root_payload.get("canonical_root") != str(resolved)
            or root_payload.get("canonical_live_data") != str(resolved_live)
        ):
            raise ValueError(
                "backup root ownership marker does not match canonical paths"
            )
        root_id = root_payload["root_id"]
        _assert_path_matches_descriptor(root_path, root_metadata, label="backup root")
        _assert_path_matches_path(
            live_path,
            live_metadata,
            label="live data directory",
        )
        if (
            _regular_descriptor_signature(
                root_marker_fd,
                expected_device=root_metadata.st_dev,
            )
            != root_marker_signature
        ):
            raise ValueError(
                "backup root ownership marker changed during retention planning"
            )
        root_names = sorted(os.listdir(root_fd))
        if len(root_names) > _MAX_RETENTION_ENTRIES:
            raise ValueError("backup root exceeds retention planning entry limit")
        names: list[str] = []
        entry_count = [0]
        for name in root_names:
            if _SNAPSHOT.fullmatch(name) is None:
                continue
            metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError("refusing timestamp-named symlink")
            if not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("timestamp-named entry must be a real directory")
            snapshot_fd = os.open(
                name,
                _directory_read_flags(),
                dir_fd=root_fd,
            )
            try:
                opened = os.fstat(snapshot_fd)
                if _identity(opened) != _identity(metadata):
                    raise ValueError("snapshot changed during retention planning")
                owner = _read_snapshot_owner_fd(snapshot_fd)
                signature = _validate_snapshot_tree(
                    snapshot_fd,
                    root_metadata.st_dev,
                    entry_count=entry_count,
                )
                if owner == root_id:
                    names.append(name)
                    held_snapshots[name] = (
                        snapshot_fd,
                        metadata,
                        owner,
                        signature,
                    )
                    snapshot_fd = -1
            finally:
                if snapshot_fd >= 0:
                    os.close(snapshot_fd)
            _assert_entry_matches_descriptor(root_fd, name, metadata)
        kept = retained_snapshot_names(names, now=now)
        candidates = sorted(set(names) - kept)
        signatures: dict[str, str] = {}
        for name in sorted(held_snapshots):
            snapshot_fd, metadata, owner, signature = held_snapshots[name]
            current_owner = _read_snapshot_owner_fd(snapshot_fd)
            current_signature = _validate_snapshot_tree(
                snapshot_fd,
                root_metadata.st_dev,
                entry_count=[0],
            )
            if current_owner != owner or current_signature != signature:
                raise ValueError("snapshot changed during retention planning")
            _assert_entry_matches_descriptor(root_fd, name, metadata)
            signatures[name] = signature
        if sorted(os.listdir(root_fd)) != root_names:
            raise ValueError("backup root changed during retention planning")
        _assert_path_matches_descriptor(root_path, root_metadata, label="backup root")
        _assert_path_matches_path(
            live_path,
            live_metadata,
            label="live data directory",
        )
    finally:
        for snapshot_fd, _, _, _ in held_snapshots.values():
            os.close(snapshot_fd)
        if root_marker_fd >= 0:
            fcntl.flock(root_marker_fd, fcntl.LOCK_UN)
            os.close(root_marker_fd)
        os.close(root_fd)
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema_version": 1,
        "observed_at": observed.isoformat(),
        "policy": {"daily": 7, "weekly": 5, "monthly": 12},
        "owned_snapshot_count": len(names),
        "kept": sorted(kept),
        "expire_candidates": candidates,
        "candidate_signatures": {
            name: signatures[name] for name in candidates
        },
        "mutation_performed": False,
        "production_reclamation_required": True,
    }


def prune_snapshots(
    root: Path | str,
    *,
    live_data: Path | str,
    apply: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for the now strictly read-only retention planner."""
    if apply:
        raise ValueError("automatic retention is read-only; apply is forbidden")
    return plan_snapshot_retention(root, live_data=live_data, now=now)


def _open_directory_no_follow(path: Path, *, label: str) -> int:
    try:
        return os.open(path, _directory_read_flags())
    except OSError as error:
        raise ValueError(f"{label} must be an existing real directory") from error


def _stat_real_directory_no_follow(
    path: Path,
    *,
    label: str,
) -> os.stat_result:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise ValueError(f"{label} must be an existing real directory") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be an existing real directory")
    return metadata


def _noatime_flag() -> int:
    flag = getattr(os, "O_NOATIME", None)
    if flag is None:
        raise ValueError(
            "strict read-only retention planning requires O_NOATIME support"
        )
    return flag


def _directory_read_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | _noatime_flag()
    )


def _open_regular_noatime_at(
    directory_fd: int,
    name: str,
    *,
    label: str,
) -> int:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | _noatime_flag(),
            dir_fd=directory_fd,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise ValueError(f"{label} is invalid")
        return descriptor
    except OSError as error:
        raise ValueError(f"{label} is missing or invalid") from error


def _assert_path_matches_descriptor(
    path: Path,
    metadata: os.stat_result,
    *,
    label: str,
) -> None:
    _assert_path_matches_path(path, metadata, label=label)


def _assert_path_matches_path(
    path: Path,
    metadata: os.stat_result,
    *,
    label: str,
) -> None:
    # Live PGDATA deliberately uses this path-based boundary check because
    # retention never reads it. Snapshot and marker files remain descriptor-
    # bound so their contents cannot be redirected between validation steps.
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise ValueError(f"{label} changed after it was opened") from error
    if (
        not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino)
    ):
        raise ValueError(f"{label} changed after it was opened")


def _read_json_descriptor(descriptor: int) -> dict[str, Any]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    data = b""
    while len(data) <= 1024 * 1024:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            break
        data += chunk
    if len(data) > 1024 * 1024:
        raise ValueError("backup root ownership marker is invalid")
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("backup root ownership marker is invalid") from error
    if not isinstance(payload, dict):
        raise ValueError("backup root ownership marker is invalid")
    return payload


def _read_snapshot_owner_fd(directory_fd: int) -> str | None:
    try:
        marker_fd = _open_regular_noatime_at(
            directory_fd,
            _SNAPSHOT_MARKER,
            label="snapshot ownership marker",
        )
    except ValueError:
        return None
    try:
        data = b""
        while True:
            chunk = os.read(marker_fd, 65536)
            if not chunk:
                break
            data += chunk
            if len(data) > 1024 * 1024:
                return None
    finally:
        os.close(marker_fd)
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None
    root_id = payload.get("root_id")
    return root_id if isinstance(root_id, str) else None


def _identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode)


def _stable_signature(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        *_identity(metadata),
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_atime_ns,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _regular_descriptor_signature(
    descriptor: int,
    *,
    expected_device: int,
) -> str:
    metadata = os.fstat(descriptor)
    if (
        metadata.st_dev != expected_device
        or metadata.st_nlink != 1
        or not stat.S_ISREG(metadata.st_mode)
    ):
        raise ValueError("regular file changed during retention planning")
    signature = _stable_signature(metadata)
    content_digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        content_digest.update(chunk)
    if _stable_signature(os.fstat(descriptor)) != signature:
        raise ValueError("regular file changed during retention planning")
    digest = hashlib.sha256()
    digest.update(json.dumps(signature).encode("ascii"))
    digest.update(content_digest.digest())
    return digest.hexdigest()


def _validate_snapshot_tree(
    directory_fd: int,
    expected_device: int,
    *,
    entry_count: list[int],
) -> str:
    """Validate one observed tree using read-only, no-follow operations."""
    before = os.fstat(directory_fd)
    if before.st_dev != expected_device or not stat.S_ISDIR(before.st_mode):
        raise ValueError("refusing snapshot containing a device boundary")
    names = sorted(os.listdir(directory_fd))
    observed: dict[str, tuple[int, ...]] = {}
    digest = hashlib.sha256()
    digest.update(json.dumps(_stable_signature(before)).encode("ascii"))
    for child in names:
        entry_count[0] += 1
        if entry_count[0] > _MAX_RETENTION_ENTRIES:
            raise ValueError("snapshot exceeds retention planning entry limit")
        try:
            metadata = os.stat(
                child,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except OSError as error:
            raise ValueError("snapshot changed during retention planning") from error
        if metadata.st_dev != expected_device:
            raise ValueError("refusing snapshot containing a device boundary")
        observed[child] = _stable_signature(metadata)
        encoded_name = child.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(json.dumps(observed[child]).encode("ascii"))
        if stat.S_ISDIR(metadata.st_mode):
            try:
                child_fd = os.open(
                    child,
                    _directory_read_flags(),
                    dir_fd=directory_fd,
                )
            except OSError as error:
                raise ValueError(
                    "snapshot changed during retention planning"
                ) from error
            try:
                if _identity(os.fstat(child_fd)) != _identity(metadata):
                    raise ValueError("snapshot changed during retention planning")
                child_signature = _validate_snapshot_tree(
                    child_fd,
                    expected_device,
                    entry_count=entry_count,
                )
                digest.update(b"D")
                digest.update(bytes.fromhex(child_signature))
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                raise ValueError("refusing regular file with unsafe link count")
            try:
                child_fd = os.open(
                    child,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | _noatime_flag(),
                    dir_fd=directory_fd,
                )
            except OSError as error:
                raise ValueError(
                    "snapshot changed during retention planning"
                ) from error
            try:
                if _stable_signature(os.fstat(child_fd)) != observed[child]:
                    raise ValueError("snapshot changed during retention planning")
                digest.update(b"F")
                digest.update(
                    bytes.fromhex(
                        _regular_descriptor_signature(
                            child_fd,
                            expected_device=expected_device,
                        )
                    )
                )
            finally:
                os.close(child_fd)
        else:
            raise ValueError(
                "refusing links or special files during retention planning"
            )
    if sorted(os.listdir(directory_fd)) != names:
        raise ValueError("snapshot changed during retention planning")
    for child, signature in observed.items():
        try:
            current = os.stat(
                child,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except OSError as error:
            raise ValueError("snapshot changed during retention planning") from error
        if _stable_signature(current) != signature:
            raise ValueError("snapshot changed during retention planning")
    if _stable_signature(os.fstat(directory_fd)) != _stable_signature(before):
        raise ValueError("snapshot changed during retention planning")
    return digest.hexdigest()


def _assert_entry_matches_descriptor(
    parent_fd: int,
    name: str,
    metadata: os.stat_result,
) -> None:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise ValueError("snapshot changed during retention planning") from error
    if _stable_signature(current) != _stable_signature(metadata):
        raise ValueError("snapshot changed during retention planning")
