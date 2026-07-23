"""Dry-run-first legacy import orchestration with an explicit cutover boundary."""

from __future__ import annotations

from typing import Any

from argus.persistence.reconcile import (
    reconcile_legacy_sessions,
    reconcile_legacy_state,
)


def _has_conflicts(reports: dict[str, dict[str, int]]) -> bool:
    return any(report["conflicting"] for report in reports.values())


def reconcile_import(
    *,
    search_source: str,
    session_source: str,
    repository,
    apply: bool = False,
) -> dict[str, Any]:
    """Reconcile both legacy stores and verify an applied import is idempotent."""
    before = {
        "search": reconcile_legacy_state(
            search_source,
            repository,
            apply=False,
        ),
        "sessions": reconcile_legacy_sessions(
            session_source,
            repository,
            apply=False,
        ),
    }
    report: dict[str, Any] = {
        "applied": False,
        "verified": False,
        "rollback_boundary": "before_production_cutover",
        "before": before,
    }
    if not apply:
        return report
    if _has_conflicts(before):
        raise ValueError("legacy reconciliation found conflicts; import refused")

    applied = {
        "search": reconcile_legacy_state(
            search_source,
            repository,
            apply=True,
        ),
        "sessions": reconcile_legacy_sessions(
            session_source,
            repository,
            apply=True,
        ),
    }
    after = {
        "search": reconcile_legacy_state(
            search_source,
            repository,
            apply=False,
        ),
        "sessions": reconcile_legacy_sessions(
            session_source,
            repository,
            apply=False,
        ),
    }
    verified = all(
        after[kind]["source"] == before[kind]["source"]
        and after[kind]["imported"] == 0
        and after[kind]["conflicting"] == 0
        for kind in ("search", "sessions")
    )
    if not verified:
        raise RuntimeError(
            "post-import reconciliation did not converge; remain before cutover"
        )
    report.update(
        {
            "applied": True,
            "verified": True,
            "applied_counts": applied,
            "after": after,
        }
    )
    return report
