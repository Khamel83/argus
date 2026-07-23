"""Pure safety helpers for PostgreSQL recovery operator tooling."""

from __future__ import annotations

import re
import socket
import json
import os
import stat
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url


_SNAPSHOT = re.compile(r"^\d{8}T\d{6}Z$")
_PROTECTED_DATABASES = {"argus", "atlas", "postgres", "template0", "template1"}
BACKUP_ROOT_MARKER = ".argus-shared-postgres-backup-root.json"


def _existing_real_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute")
    if path.is_symlink():
        raise ValueError(f"{label} cannot be a symlink")
    try:
        metadata = path.stat()
    except FileNotFoundError as error:
        raise ValueError(f"{label} must exist") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a real directory")
    return path.resolve(strict=True)


def _separate_backup_paths(root: Path | str, live_data: Path | str) -> tuple[Path, Path]:
    root_path = _existing_real_directory(Path(root), label="backup root")
    live_path = _existing_real_directory(Path(live_data), label="live data directory")
    if (
        root_path == Path("/")
        or len(root_path.parts) < 3
        or root_path == live_path
        or root_path in live_path.parents
        or live_path in root_path.parents
    ):
        raise ValueError("backup root must be canonically outside live data")
    root_metadata = root_path.stat()
    if root_metadata.st_uid != os.geteuid() or root_metadata.st_mode & 0o022:
        raise ValueError("backup root must be operator-owned and not group/world writable")
    return root_path, live_path


def _read_root_marker(root: Path) -> dict[str, str]:
    marker = root / BACKUP_ROOT_MARKER
    if marker.is_symlink():
        raise ValueError("backup root marker cannot be a symlink")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError("backup root ownership marker is missing") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("backup root ownership marker is invalid") from error
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("root_id"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", payload["root_id"])
    ):
        raise ValueError("backup root ownership marker is invalid")
    return payload


def initialize_backup_root(
    root: Path | str,
    *,
    live_data: Path | str,
) -> dict[str, str]:
    """Claim an existing, separate, operator-owned directory for Argus backups."""
    resolved_root, resolved_live = _separate_backup_paths(root, live_data)
    marker_payload = {
        "schema_version": 1,
        "root_id": uuid.uuid4().hex,
        "canonical_root": str(resolved_root),
        "canonical_live_data": str(resolved_live),
    }
    root_descriptor = os.open(
        resolved_root,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        descriptor = os.open(
            BACKUP_ROOT_MARKER,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=root_descriptor,
        )
        try:
            os.write(
                descriptor,
                (json.dumps(marker_payload, sort_keys=True) + "\n").encode(),
            )
            os.fsync(descriptor)
            os.fsync(root_descriptor)
        except Exception:
            try:
                os.unlink(BACKUP_ROOT_MARKER, dir_fd=root_descriptor)
            except OSError:
                pass
            raise
        finally:
            os.close(descriptor)
    finally:
        os.close(root_descriptor)
    return marker_payload


def validate_scratch_database(name: str, *, tenant: str = "argus") -> str:
    """Accept only explicit disposable Argus restore targets."""
    if tenant not in {"argus", "atlas"}:
        raise ValueError("scratch tenant must be argus or atlas")
    pattern = re.compile(
        rf"^{tenant}_restore_[a-z0-9][a-z0-9_]{{5,54}}$"
    )
    if name in _PROTECTED_DATABASES or pattern.fullmatch(name) is None:
        raise ValueError(
            f"scratch database must match {tenant}_restore_<operator-purpose> "
            "and cannot name a protected database"
        )
    return name


def validate_database_name(
    name: str,
    *,
    allowed: set[str] | None = None,
) -> str:
    """Accept a plain PostgreSQL database identifier, never a URI or options."""
    if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name) is None:
        raise ValueError("database input must be a plain database name")
    if allowed is not None and name not in allowed:
        raise ValueError("database name is outside the approved set")
    return name


def validate_credential_free_database_url(
    value: str,
    *,
    allowed_database: str | None = None,
) -> str:
    """Reject URL-embedded identities, secrets, and query-string material."""
    try:
        parsed = make_url(value)
    except Exception as error:
        raise ValueError("database URL must be credential-free") from error
    if parsed.username is not None or parsed.password is not None or parsed.query:
        raise ValueError("database URL must be credential-free")
    if allowed_database is not None and parsed.database != allowed_database:
        raise ValueError("database URL must target the approved database")
    return value


def validate_backup_root(
    root: Path | str,
    *,
    live_data: Path | str,
) -> Path:
    """Validate a claimed backup root and its canonical separation from PGDATA."""
    resolved_root, resolved_live = _separate_backup_paths(root, live_data)
    marker = _read_root_marker(resolved_root)
    if (
        marker.get("canonical_root") != str(resolved_root)
        or marker.get("canonical_live_data") != str(resolved_live)
    ):
        raise ValueError("backup root ownership marker does not match canonical paths")
    return resolved_root


def _resolve(host: str) -> set[str]:
    return {
        address[4][0]
        for address in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    }


def validate_compatibility_alias(
    primary: str,
    compatibility: str,
    *,
    resolver: Callable[[str], set[str]] = _resolve,
) -> dict[str, object]:
    """Prove the temporary compatibility name resolves to the primary endpoint."""
    primary_addresses = resolver(primary)
    compatibility_addresses = resolver(compatibility)
    if not primary_addresses or primary_addresses != compatibility_addresses:
        raise ValueError("compatibility alias does not resolve to the same endpoint")
    return {
        "primary": primary,
        "compatibility": compatibility,
        "valid": True,
    }


def retained_snapshot_names(
    names: Iterable[str],
    *,
    now: datetime | None = None,
) -> set[str]:
    """Select the union of 7 daily, 5 weekly, and 12 monthly restore sets."""
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    parsed = []
    for name in names:
        if _SNAPSHOT.fullmatch(name):
            parsed.append(
                (datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), name)
            )
    future = {name for timestamp, name in parsed if timestamp > observed_at}
    parsed = sorted(
        (item for item in parsed if item[0] <= observed_at),
        reverse=True,
    )

    def newest_per(key, count: int) -> set[str]:
        selected: dict[object, str] = {}
        for timestamp, name in parsed:
            selected.setdefault(key(timestamp), name)
            if len(selected) == count:
                break
        return set(selected.values())

    daily = newest_per(lambda value: value.date(), 7)
    weekly = newest_per(lambda value: value.isocalendar()[:2], 5)
    monthly = newest_per(lambda value: (value.year, value.month), 12)
    return daily | weekly | monthly | future
