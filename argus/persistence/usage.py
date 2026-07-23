"""Dialect-neutral administrative usage queries over authoritative run rows."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from argus.persistence.search_ledger import (
    ExtractionArtifactRow,
    ExtractionRunRow,
    NormalizedResultRow,
    ProviderAttemptRow,
    ResultProvenanceRow,
    RetrievalRequestRow,
    RetrievalRunRow,
)


class SqlAlchemyUsageRepository:
    """Aggregate logical operations, never result-row fan-out."""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    @staticmethod
    def _cutoff(days: int) -> datetime:
        return datetime.now(tz=None) - timedelta(days=max(0, int(days)))

    def _operations(self, days: int) -> list[dict[str, Any]]:
        cutoff = self._cutoff(days)
        operations: list[dict[str, Any]] = []
        with self.session_factory() as session:
            searches = session.execute(
                select(
                    RetrievalRunRow.id,
                    RetrievalRunRow.committed_at,
                    RetrievalRunRow.status,
                    RetrievalRequestRow.caller,
                    ResultProvenanceRow.machine,
                )
                .join(
                    RetrievalRequestRow,
                    RetrievalRequestRow.id == RetrievalRunRow.request_id,
                )
                .outerjoin(
                    NormalizedResultRow,
                    NormalizedResultRow.run_id == RetrievalRunRow.id,
                )
                .outerjoin(
                    ResultProvenanceRow,
                    ResultProvenanceRow.result_id == NormalizedResultRow.id,
                )
                .where(RetrievalRunRow.committed_at >= cutoff)
                .order_by(RetrievalRunRow.id, NormalizedResultRow.final_rank)
            )
            seen = set()
            for run_id, committed_at, status, caller, machine in searches:
                if run_id in seen:
                    continue
                seen.add(run_id)
                operations.append(
                    {
                        "kind": "search",
                        "created_at": committed_at,
                        "status": status,
                        "caller": caller or "unknown",
                        "machine": machine or "unknown",
                    }
                )

            extractions = session.execute(
                select(ExtractionRunRow, ExtractionArtifactRow.machine)
                .outerjoin(
                    ExtractionArtifactRow,
                    ExtractionArtifactRow.run_id == ExtractionRunRow.id,
                )
                .where(ExtractionRunRow.committed_at >= cutoff)
            )
            for run, machine in extractions:
                operations.append(
                    {
                        "kind": "extraction",
                        "created_at": run.committed_at,
                        "status": run.status,
                        "caller": run.caller or "unknown",
                        "machine": machine or "unknown",
                    }
                )
        return operations

    def get_daily_operation_counts(self, days: int = 30) -> list[dict[str, Any]]:
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for operation in self._operations(days):
            counts[
                (
                    operation["created_at"].date().isoformat(),
                    operation["machine"],
                )
            ] += 1
        return [
            {"day": day, "machine": machine, "count": count}
            for (day, machine), count in sorted(counts.items())
        ]

    def get_machine_summary(self, days: int = 30) -> list[dict[str, Any]]:
        today = datetime.now(tz=None).date()
        summaries: dict[str, dict[str, Any]] = {}
        for operation in self._operations(days):
            machine = operation["machine"]
            summary = summaries.setdefault(
                machine,
                {"machine": machine, "total": 0, "today": 0, "last_seen": None},
            )
            summary["total"] += 1
            if operation["created_at"].date() == today:
                summary["today"] += 1
            if (
                summary["last_seen"] is None
                or operation["created_at"] > summary["last_seen"]
            ):
                summary["last_seen"] = operation["created_at"]
        return sorted(summaries.values(), key=lambda row: row["total"], reverse=True)

    def get_provider_activity(self, days: int = 7) -> list[dict[str, Any]]:
        cutoff = self._cutoff(days)
        groups: dict[str, dict[str, Any]] = {}
        with self.session_factory() as session:
            rows = session.execute(
                select(ProviderAttemptRow, RetrievalRunRow.committed_at)
                .join(RetrievalRunRow, RetrievalRunRow.id == ProviderAttemptRow.run_id)
                .where(
                    RetrievalRunRow.committed_at >= cutoff,
                    ProviderAttemptRow.status != "skipped",
                )
            )
            for attempt, _ in rows:
                group = groups.setdefault(
                    attempt.provider,
                    {
                        "provider": attempt.provider,
                        "calls": 0,
                        "successes": 0,
                        "_latency": 0,
                    },
                )
                group["calls"] += 1
                group["successes"] += int(attempt.status == "success")
                group["_latency"] += attempt.latency_ms
        result = []
        for group in groups.values():
            calls = group["calls"]
            result.append(
                {
                    "provider": group["provider"],
                    "calls": calls,
                    "successes": group["successes"],
                    "avg_latency_ms": round(group["_latency"] / calls) if calls else 0,
                    "success_rate": (
                        round(100.0 * group["successes"] / calls, 1) if calls else 0.0
                    ),
                }
            )
        return sorted(result, key=lambda row: row["calls"], reverse=True)

    def get_caller_activity(self, days: int = 7) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for operation in self._operations(days):
            caller = operation["caller"]
            group = groups.setdefault(
                caller,
                {"caller": caller, "attempted": 0, "successes": 0},
            )
            group["attempted"] += 1
            group["successes"] += int(
                operation["status"] in {"accepted", "succeeded"}
            )
        for group in groups.values():
            attempted = group["attempted"]
            group["success_rate"] = (
                round(100.0 * group["successes"] / attempted, 1)
                if attempted
                else 0.0
            )
        return sorted(groups.values(), key=lambda row: row["attempted"], reverse=True)
