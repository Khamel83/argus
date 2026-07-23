"""Atomic recovery evidence records and bounded backup retention."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.recovery.artifacts import load_verified_backup_set
from argus.recovery.operator import (
    BACKUP_ROOT_MARKER,
    retained_snapshot_names,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SNAPSHOT = re.compile(r"^\d{8}T\d{6}Z$")
_SNAPSHOT_MARKER = ".argus-backup-set.json"
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
        "backup_manifest_sha256": value.get("backup_manifest_sha256"),
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


def _record_restore(
    path: Path | str,
    *,
    schema_head: str,
    expected_manifest_sha256: str,
    verified_at: datetime | None = None,
) -> None:
    """Record successful checks; callers invoke this only after every verifier exits zero."""
    if not schema_head or len(schema_head) > 128:
        raise ValueError("schema_head is invalid")
    timestamp = (verified_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    evidence_path = Path(path)
    payload = _existing(evidence_path)
    backup = payload.get("backup")
    if (
        not isinstance(backup, dict)
        or backup.get("manifest_sha256") != expected_manifest_sha256
    ):
        raise ValueError("backup evidence changed during restore verification")
    payload["restore"] = {
        "verified_at": timestamp.isoformat(),
        "databases": ["atlas", "argus"],
        "globals_validated": True,
        "schema_head": schema_head.strip(),
        "backup_manifest_sha256": expected_manifest_sha256,
        "checks": {name: True for name in _RESTORE_CHECKS},
    }
    _atomic_write(evidence_path, payload)


def record_verified_backup(
    path: Path | str,
    *,
    backup_set: Path | str,
    root: Path | str,
    live_data: Path | str,
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
) -> None:
    """Verify restored databases against their source manifest before evidence."""
    from argus.recovery.database import (
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
            lambda database, expected: verify_argus_database(database)
        )
        atlas_verifier = verify_atlas or (
            lambda database, expected: verify_atlas_database(
                database,
                expected_inventory=expected,
            )
        )
        argus_report = argus_verifier(
            argus_name,
            None,
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
        _record_restore(
            evidence_path,
            schema_head=str(argus_report["schema_head"]),
            expected_manifest_sha256=expected_manifest_sha256,
            verified_at=verified_at,
        )


def prune_snapshots(
    root: Path | str,
    *,
    live_data: Path | str,
    apply: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Prune only owned snapshot directories under a validated backup root."""
    root_path = Path(root)
    live_path = Path(live_data)
    if not root_path.is_absolute() or not live_path.is_absolute():
        raise ValueError("backup root and live data directory must be absolute")
    root_fd = _open_directory_no_follow(root_path, label="backup root")
    live_fd = -1
    removed: list[str] = []
    try:
        root_metadata = os.fstat(root_fd)
        _assert_path_matches_descriptor(root_path, root_metadata, label="backup root")
        if root_metadata.st_uid != os.geteuid() or root_metadata.st_mode & 0o022:
            raise ValueError(
                "backup root must be operator-owned and not group/world writable"
            )
        resolved = root_path.resolve(strict=True)
        live_fd = _open_directory_no_follow(live_path, label="live data directory")
        live_metadata = os.fstat(live_fd)
        _assert_path_matches_descriptor(
            live_path,
            live_metadata,
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
        root_payload = _read_json_at(root_fd, BACKUP_ROOT_MARKER)
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
        _assert_path_matches_descriptor(
            live_path,
            live_metadata,
            label="live data directory",
        )
        names = []
        for name in sorted(os.listdir(root_fd)):
            if _SNAPSHOT.fullmatch(name) is None:
                continue
            metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError("refusing timestamp-named symlink")
            if not stat.S_ISDIR(metadata.st_mode):
                continue
            if _read_snapshot_owner_at(root_fd, name) == root_id:
                names.append(name)
        kept = retained_snapshot_names(names, now=now)
        candidates = sorted(set(names) - kept)
        if apply:
            for name in candidates:
                _assert_path_matches_descriptor(
                    root_path,
                    root_metadata,
                    label="backup root",
                )
                _assert_path_matches_descriptor(
                    live_path,
                    live_metadata,
                    label="live data directory",
                )
                if _remove_owned_snapshot(
                    root_fd,
                    name,
                    root_id,
                    root_device=root_metadata.st_dev,
                    root_path=root_path,
                    root_metadata=root_metadata,
                ):
                    removed.append(name)
        else:
            removed = candidates
    finally:
        if live_fd >= 0:
            os.close(live_fd)
        os.close(root_fd)
    return {
        "applied": apply,
        "kept": sorted(kept),
        "removed": removed,
    }


def _open_directory_no_follow(path: Path, *, label: str) -> int:
    try:
        return os.open(
            path,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as error:
        raise ValueError(f"{label} must be an existing real directory") from error


def _assert_path_matches_descriptor(
    path: Path,
    metadata: os.stat_result,
    *,
    label: str,
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise ValueError(f"{label} changed after it was opened") from error
    if (
        not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino)
    ):
        raise ValueError(f"{label} changed after it was opened")


def _read_json_at(directory_fd: int, name: str) -> dict[str, Any]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("backup root ownership marker is invalid")
            data = b""
            while len(data) <= 1024 * 1024:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                data += chunk
            if len(data) > 1024 * 1024:
                raise ValueError("backup root ownership marker is invalid")
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ValueError("backup root ownership marker is missing or invalid") from error
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("backup root ownership marker is invalid") from error
    if not isinstance(payload, dict):
        raise ValueError("backup root ownership marker is invalid")
    return payload


def _read_snapshot_owner_at(directory_fd: int, name: str) -> str | None:
    """Read an owned snapshot marker without following either directory symlink."""
    try:
        child_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
    except OSError:
        return None
    try:
        return _read_snapshot_owner_fd(child_fd)
    finally:
        os.close(child_fd)


def _read_snapshot_owner_fd(directory_fd: int) -> str | None:
    try:
        marker_fd = os.open(
            _SNAPSHOT_MARKER,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
    except OSError:
        return None
    try:
        metadata = os.fstat(marker_fd)
        if not stat.S_ISREG(metadata.st_mode):
            return None
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


def _remove_owned_snapshot(
    directory_fd: int,
    name: str,
    root_id: str,
    *,
    root_device: int,
    root_path: Path,
    root_metadata: os.stat_result,
) -> bool:
    """Atomically quarantine, revalidate, then remove one owned snapshot."""
    if _SNAPSHOT.fullmatch(name) is None:
        raise ValueError("refusing unsafe retention target")
    original_metadata = os.stat(
        name,
        dir_fd=directory_fd,
        follow_symlinks=False,
    )
    if not stat.S_ISDIR(original_metadata.st_mode):
        raise ValueError("refusing non-directory retention target")
    quarantine = f".pruning.{name}.{uuid.uuid4().hex}"
    os.rename(name, quarantine, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    try:
        quarantine_fd = os.open(
            quarantine,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
    except OSError:
        return False
    try:
        quarantine_metadata = os.fstat(quarantine_fd)
        if _identity(quarantine_metadata) != _identity(original_metadata):
            return False
        if _read_snapshot_owner_fd(quarantine_fd) != root_id:
            _restore_quarantine(
                directory_fd,
                quarantine,
                name,
                quarantine_metadata,
            )
            return False
        if not _tree_stays_on_device_fd(quarantine_fd, root_device):
            _restore_quarantine(
                directory_fd,
                quarantine,
                name,
                quarantine_metadata,
            )
            raise ValueError("refusing snapshot containing a device boundary")
        _assert_path_matches_descriptor(
            root_path,
            root_metadata,
            label="backup root",
        )
        _delete_tree_fd(
            directory_fd,
            quarantine,
            quarantine_fd,
            expected_device=root_device,
        )
        return True
    finally:
        os.close(quarantine_fd)


def _restore_quarantine(
    directory_fd: int,
    quarantine: str,
    name: str,
    expected: os.stat_result,
) -> None:
    try:
        current = os.stat(
            quarantine,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if _identity(current) != _identity(expected):
            return
        os.rename(
            quarantine,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
    except OSError:
        pass


def _identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode)


def _tree_stays_on_device_fd(
    directory_fd: int,
    expected_device: int,
) -> bool:
    if os.fstat(directory_fd).st_dev != expected_device:
        return False
    try:
        for child in os.listdir(directory_fd):
            child_metadata = os.stat(
                child,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if child_metadata.st_dev != expected_device:
                return False
            if stat.S_ISDIR(child_metadata.st_mode):
                child_fd = os.open(
                    child,
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=directory_fd,
                )
                try:
                    if _identity(os.fstat(child_fd)) != _identity(child_metadata):
                        return False
                    if not _tree_stays_on_device_fd(
                        child_fd,
                        expected_device,
                    ):
                        return False
                finally:
                    os.close(child_fd)
        return True
    except OSError:
        return False


def _assert_entry_matches_descriptor(
    parent_fd: int,
    name: str,
    descriptor: int,
) -> None:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise ValueError(
            "snapshot changed during descriptor-bound deletion"
        ) from error
    if _identity(current) != _identity(os.fstat(descriptor)):
        raise ValueError("snapshot changed during descriptor-bound deletion")


def _delete_tree_fd(
    parent_fd: int,
    name: str,
    directory_fd: int,
    *,
    expected_device: int,
) -> None:
    """Delete through held descriptors and fd-relative, identity-checked names."""
    _assert_entry_matches_descriptor(parent_fd, name, directory_fd)
    for child in sorted(os.listdir(directory_fd)):
        metadata = os.stat(
            child,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if metadata.st_dev != expected_device:
            raise ValueError("refusing snapshot containing a device boundary")
        tombstone = f".deleting.{uuid.uuid4().hex}"
        os.rename(
            child,
            tombstone,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        moved = os.stat(
            tombstone,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if _identity(moved) != _identity(metadata):
            raise ValueError("snapshot changed during descriptor-bound deletion")
        if stat.S_ISDIR(moved.st_mode):
            child_fd = os.open(
                tombstone,
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
            try:
                if _identity(os.fstat(child_fd)) != _identity(moved):
                    raise ValueError(
                        "snapshot changed during descriptor-bound deletion"
                    )
                _delete_tree_fd(
                    directory_fd,
                    tombstone,
                    child_fd,
                    expected_device=expected_device,
                )
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(moved.st_mode):
            child_fd = os.open(
                tombstone,
                os.O_RDONLY
                | os.O_NONBLOCK
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
            try:
                if _identity(os.fstat(child_fd)) != _identity(moved):
                    raise ValueError(
                        "snapshot changed during descriptor-bound deletion"
                    )
                _assert_entry_matches_descriptor(
                    directory_fd,
                    tombstone,
                    child_fd,
                )
                os.unlink(tombstone, dir_fd=directory_fd)
            finally:
                os.close(child_fd)
        elif stat.S_ISLNK(moved.st_mode):
            current = os.stat(
                tombstone,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if _identity(current) != _identity(moved):
                raise ValueError(
                    "snapshot changed during descriptor-bound deletion"
                )
            os.unlink(tombstone, dir_fd=directory_fd)
        else:
            raise ValueError(
                "refusing unsupported file type during snapshot deletion"
            )
    _assert_entry_matches_descriptor(parent_fd, name, directory_fd)
    os.rmdir(name, dir_fd=parent_fd)
