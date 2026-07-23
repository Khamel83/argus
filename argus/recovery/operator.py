"""Pure safety helpers for PostgreSQL recovery operator tooling."""

from __future__ import annotations

import re
import socket
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path


_SNAPSHOT = re.compile(r"^\d{8}T\d{6}Z$")
_PROTECTED_DATABASES = {"argus", "atlas", "postgres", "template0", "template1"}


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


def validate_backup_root(
    root: Path | str,
    *,
    live_data: Path | str,
) -> Path:
    """Require a canonical backup destination outside the live data tree."""
    root_path = Path(root)
    live_path = Path(live_data)
    if not root_path.is_absolute() or not live_path.is_absolute():
        raise ValueError("backup root and live data directory must be absolute")
    resolved_root = root_path.resolve()
    resolved_live = live_path.resolve()
    if (
        resolved_root == Path("/")
        or len(resolved_root.parts) < 3
        or resolved_root == resolved_live
        or resolved_live in resolved_root.parents
    ):
        raise ValueError("backup root must be canonically outside live data")
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
