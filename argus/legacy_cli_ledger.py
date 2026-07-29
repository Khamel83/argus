"""Explicit non-production compatibility boundary for local ledger migration."""

from __future__ import annotations

import os
from pathlib import Path

import click

from argus.config import get_config
from argus.persistence.reconcile import (
    reconcile_legacy_sessions,
    reconcile_legacy_state,
)
from argus.persistence.search_ledger import (
    create_read_only_search_ledger_repository,
    create_search_ledger_repository,
)


def _repository(target: str | None, apply: bool):
    target_url = target or get_config().db_url
    if not apply and target_url.startswith("sqlite:///"):
        target_path = Path(target_url.removeprefix("sqlite:///"))
        if target_path.exists():
            return create_read_only_search_ledger_repository(target_url)
        return create_search_ledger_repository("sqlite:///:memory:", create_schema=True)
    return create_search_ledger_repository(
        target_url,
        create_schema=False if not apply else None,
    )


def _require_nonproduction() -> None:
    if os.environ.get("ARGUS_ENV", "development").strip().lower() == "production":
        raise click.ClickException(
            "Production ledger migration belongs to the HTTP authority"
        )
    if os.environ.get("ARGUS_LEGACY_CLI_MIGRATIONS", "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        raise click.ClickException(
            "Local ledger migration requires ARGUS_LEGACY_CLI_MIGRATIONS=true"
        )


def reconcile_legacy_cli(source: str, target: str | None, apply: bool) -> dict:
    _require_nonproduction()
    return reconcile_legacy_state(source, _repository(target, apply), apply=apply)


def reconcile_sessions_cli(source: str, target: str | None, apply: bool) -> dict:
    _require_nonproduction()
    return reconcile_legacy_sessions(source, _repository(target, apply), apply=apply)
