"""Failure-tolerant dashboard views over authoritative persistence."""

from __future__ import annotations

from typing import Any

from argus.logging import get_logger
from argus.persistence.search_ledger import create_search_ledger_repository
from argus.persistence.usage import SqlAlchemyUsageRepository

logger = get_logger("api.usage")


def _query(method: str, *, days: int) -> list[dict[str, Any]]:
    try:
        repository = create_search_ledger_repository(create_schema=False)
        usage = SqlAlchemyUsageRepository(repository.session_factory)
        return getattr(usage, method)(days=days)
    except Exception as exc:
        logger.warning("usage: %s failed: %s", method, exc)
        return []


def get_daily_query_counts(days: int = 30) -> list[dict[str, Any]]:
    """Return logical search and extraction run counts grouped by day/machine."""
    return _query("get_daily_operation_counts", days=days)


def get_machine_summary(days: int = 30) -> list[dict[str, Any]]:
    return _query("get_machine_summary", days=days)


def get_provider_activity(days: int = 7) -> list[dict[str, Any]]:
    return _query("get_provider_activity", days=days)


def get_caller_activity(days: int = 7) -> list[dict[str, Any]]:
    return _query("get_caller_activity", days=days)
