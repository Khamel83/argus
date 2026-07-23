"""Dry-run-first import of legacy search persistence into the search ledger."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import create_engine, inspect, select, text

from argus.models import (
    ProviderName,
    ProviderTrace,
    SearchMode,
    SearchQuery,
    SearchResponse,
    SearchResult,
)
from argus.persistence.search_ledger import (
    NormalizedResultRow,
    RetrievalRequestRow,
    RetrievalRunRow,
    SqlAlchemySearchLedgerRepository,
)


def reconcile_legacy_state(
    source_url: str,
    repository: SqlAlchemySearchLedgerRepository,
    *,
    apply: bool = False,
) -> dict[str, int]:
    """Report or import legacy completed search runs.

    A run is the reconciliation unit. Dry-run is the default; ``apply=True``
    writes each missing run through the same atomic repository used by HTTP.
    """
    source_engine = create_engine(source_url)
    required = {"search_queries", "search_runs", "search_results"}
    if not required <= set(inspect(source_engine).get_table_names()):
        raise ValueError("source does not contain the legacy search tables")

    report = {"source": 0, "imported": 0, "skipped": 0, "conflicting": 0}
    with source_engine.connect() as connection:
        runs = connection.execute(
            text(
                "SELECT r.id, r.search_run_id, r.cached, r.finished_at, "
                "q.query_text, q.mode, q.max_results "
                "FROM search_runs r JOIN search_queries q ON q.id = r.query_id "
                "ORDER BY r.id"
            )
        ).mappings().all()
        has_attempts = "provider_usage" in inspect(source_engine).get_table_names()

        for run in runs:
            report["source"] += 1
            results = connection.execute(
                text(
                    "SELECT url, title, snippet, domain, provider, score, final_rank, "
                    "egress, machine, metadata_json FROM search_results "
                    "WHERE run_id = :run_id ORDER BY final_rank"
                ),
                {"run_id": run["id"]},
            ).mappings().all()

            state = _target_state(repository, run["search_run_id"])
            expected = (
                run["query_text"],
                run["mode"],
                tuple(row["url"] for row in results),
            )
            if state is not None:
                report["skipped" if state == expected else "conflicting"] += 1
                continue

            report["imported"] += 1
            if not apply:
                continue

            traces = []
            if has_attempts:
                attempts = connection.execute(
                    text(
                        "SELECT provider, status, results_count, latency_ms, error, "
                        "budget_remaining, egress FROM provider_usage "
                        "WHERE run_id = :run_id ORDER BY id"
                    ),
                    {"run_id": run["id"]},
                ).mappings()
                for attempt in attempts:
                    provider = _provider(attempt["provider"])
                    if provider is None:
                        continue
                    traces.append(
                        ProviderTrace(
                            provider=provider,
                            status=attempt["status"],
                            results_count=attempt["results_count"] or 0,
                            latency_ms=attempt["latency_ms"] or 0,
                            error=attempt["error"],
                            budget_remaining=attempt["budget_remaining"],
                            egress=attempt["egress"] or "local",
                        )
                    )

            normalized = []
            for row in results:
                metadata = json.loads(row["metadata_json"] or "{}")
                if row["egress"] is not None:
                    metadata.setdefault("egress", row["egress"])
                if row["machine"] is not None:
                    metadata.setdefault("machine", row["machine"])
                normalized.append(
                    SearchResult(
                        url=row["url"],
                        title=row["title"] or "",
                        snippet=row["snippet"] or "",
                        domain=row["domain"] or "",
                        provider=_provider(row["provider"]),
                        score=row["score"] or 0.0,
                        metadata=metadata,
                    )
                )

            mode = SearchMode(run["mode"])
            query = SearchQuery(
                query=run["query_text"],
                mode=mode,
                max_results=run["max_results"],
                caller="legacy-import",
            )
            created_at = _as_datetime(run["finished_at"])
            repository.accept(
                query,
                SearchResponse(
                    query=query.query,
                    mode=mode,
                    results=normalized,
                    traces=traces,
                    total_results=len(normalized),
                    cached=bool(run["cached"]),
                    search_run_id=run["search_run_id"],
                    created_at=created_at,
                ),
            )
    return report


def _target_state(repository, search_run_id):
    with repository.session_factory() as session:
        row = session.execute(
            select(
                RetrievalRequestRow.query_text,
                RetrievalRequestRow.mode,
                RetrievalRunRow.id,
            )
            .join(
                RetrievalRunRow,
                RetrievalRunRow.request_id == RetrievalRequestRow.id,
            )
            .where(RetrievalRunRow.search_run_id == search_run_id)
        ).one_or_none()
        if row is None:
            return None
        urls = session.scalars(
            select(NormalizedResultRow.url)
            .where(NormalizedResultRow.run_id == row.id)
            .order_by(NormalizedResultRow.final_rank)
        ).all()
        return row.query_text, row.mode, tuple(urls)


def _provider(value):
    try:
        return ProviderName(value)
    except (TypeError, ValueError):
        return None


def _as_datetime(value):
    if isinstance(value, datetime):
        return value
    if value:
        return datetime.fromisoformat(value)
    return datetime.now(tz=None)
