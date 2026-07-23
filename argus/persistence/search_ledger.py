"""Atomic persistence for accepted search retrievals."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from argus.config import get_config
from argus.models import SearchQuery, SearchResponse


class LedgerBase(DeclarativeBase):
    pass


class RetrievalRequestRow(LedgerBase):
    __tablename__ = "retrieval_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(50), nullable=False)
    max_results: Mapped[int] = mapped_column(Integer, nullable=False)
    caller: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RetrievalRunRow(LedgerBase):
    __tablename__ = "retrieval_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_requests.id"), nullable=False, unique=True
    )
    search_run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    acceptance_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    total_results: Mapped[int] = mapped_column(Integer, nullable=False)
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ProviderAttemptRow(LedgerBase):
    __tablename__ = "provider_attempts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("retrieval_runs.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    results_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_remaining: Mapped[float | None] = mapped_column(Float, nullable=True)
    egress: Mapped[str] = mapped_column(String(50), nullable=False, default="local")


class ContentIdentityRow(LedgerBase):
    __tablename__ = "content_identities"

    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class NormalizedResultRow(LedgerBase):
    __tablename__ = "normalized_results"
    __table_args__ = (UniqueConstraint("run_id", "final_rank"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("retrieval_runs.id"), nullable=False)
    content_hash: Mapped[str] = mapped_column(
        ForeignKey("content_identities.content_hash"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    domain: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    final_rank: Mapped[int] = mapped_column(Integer, nullable=False)


class ResultProvenanceRow(LedgerBase):
    __tablename__ = "result_provenance"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    result_id: Mapped[str] = mapped_column(
        ForeignKey("normalized_results.id"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    egress: Mapped[str | None] = mapped_column(String(50), nullable=True)
    machine: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="search"
    )
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class DeliveryIntentRow(LedgerBase):
    __tablename__ = "delivery_intents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_runs.id"), nullable=False, unique=True
    )
    destination: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


@dataclass(frozen=True)
class AcceptanceReceipt:
    run_id: str
    delivery_intent_id: str


@dataclass(frozen=True)
class DurableAcceptanceSnapshot:
    stored_fingerprint: str | None
    state: dict


class AcceptanceConflictError(RuntimeError):
    """A public run ID was already committed with a different payload."""


def acceptance_state(query: SearchQuery, response: SearchResponse) -> dict:
    """Return the canonical immutable state written by ``accept``."""
    run_id = response.search_run_id
    if not run_id:
        raise ValueError("accepted retrieval requires a search_run_id")

    attempts = [
        {
            "provider": trace.provider.value,
            "status": trace.status,
            "results_count": trace.results_count,
            "latency_ms": trace.latency_ms,
            "error": trace.error,
            "budget_remaining": trace.budget_remaining,
            "egress": trace.egress,
        }
        for trace in response.traces
    ]
    attempts.sort(key=_canonical_json)

    results = []
    for rank, result in enumerate(response.results):
        if not result.url:
            raise ValueError("normalized search results require a URL")
        canonical_url = result.url.strip()
        content_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        provider = result.provider.value if result.provider else ""
        metadata = _normalize_json_value(dict(result.metadata))
        results.append(
            {
                "normalized": {
                    "url": result.url,
                    "title": result.title,
                    "snippet": result.snippet,
                    "domain": result.domain,
                    "provider": provider,
                    "score": result.score,
                    "final_rank": rank,
                },
                "content_identity": {
                    "content_hash": content_hash,
                    "canonical_url": canonical_url,
                },
                "provenance": {
                    "provider": provider,
                    "egress": metadata.get("egress"),
                    "machine": metadata.get("machine"),
                    "source_type": metadata.get("source_type", "search"),
                    "metadata": metadata,
                },
            }
        )

    return {
        "request": {
            "query_text": query.query,
            "mode": query.mode.value,
            "max_results": query.max_results,
            "caller": query.caller,
        },
        "run": {
            "search_run_id": run_id,
            "status": "accepted",
            "total_results": len(response.results),
            "cached": response.cached,
        },
        "attempts": attempts,
        "results": results,
        "delivery_intent": {
            "destination": "maya",
            "status": "pending",
            "payload": {
                "search_run_id": run_id,
                "result_count": len(response.results),
            },
        },
    }


def acceptance_fingerprint(state: dict) -> str:
    return hashlib.sha256(_canonical_json(state).encode("utf-8")).hexdigest()


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _normalize_json_value(value):
    return json.loads(_canonical_json(value))


def _parse_json_value(value: str | None):
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {"__invalid_json__": value}


class SearchLedgerRepository(Protocol):
    def accept(
        self, query: SearchQuery, response: SearchResponse
    ) -> AcceptanceReceipt: ...


class SqlAlchemySearchLedgerRepository:
    """Store one accepted retrieval in one database transaction."""

    def __init__(self, session_factory: sessionmaker):
        self.session_factory = session_factory

    def accept(
        self, query: SearchQuery, response: SearchResponse
    ) -> AcceptanceReceipt:
        fingerprint = acceptance_fingerprint(acceptance_state(query, response))
        try:
            return self._accept_once(query, response, fingerprint)
        except IntegrityError:
            # A concurrent request with the same public run ID may win the
            # unique-key race after our initial lookup. Acknowledge only after
            # its complete transaction, including delivery intent, is visible.
            for _ in range(100):
                receipt = self._existing_receipt(
                    response.search_run_id,
                    fingerprint,
                )
                if receipt is not None:
                    return receipt
                time.sleep(0.01)
            raise

    def _accept_once(
        self,
        query: SearchQuery,
        response: SearchResponse,
        fingerprint: str,
    ) -> AcceptanceReceipt:
        run_id = response.search_run_id
        if not run_id:
            raise ValueError("accepted retrieval requires a search_run_id")

        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(RetrievalRunRow).where(
                    RetrievalRunRow.search_run_id == run_id
                )
            )
            if existing is not None:
                return self._receipt_for_existing(
                    session,
                    existing,
                    fingerprint,
                )

            now = datetime.now(tz=None)
            request_id = uuid.uuid4().hex
            ledger_run_id = uuid.uuid4().hex
            session.add(
                RetrievalRequestRow(
                    id=request_id,
                    query_text=query.query,
                    mode=query.mode.value,
                    max_results=query.max_results,
                    caller=query.caller,
                    created_at=now,
                )
            )
            session.add(
                RetrievalRunRow(
                    id=ledger_run_id,
                    request_id=request_id,
                    search_run_id=run_id,
                    acceptance_fingerprint=fingerprint,
                    status="accepted",
                    total_results=len(response.results),
                    cached=response.cached,
                    started_at=response.created_at,
                    committed_at=now,
                )
            )

            for trace in response.traces:
                session.add(
                    ProviderAttemptRow(
                        id=uuid.uuid4().hex,
                        run_id=ledger_run_id,
                        provider=trace.provider.value,
                        status=trace.status,
                        results_count=trace.results_count,
                        latency_ms=trace.latency_ms,
                        error=trace.error,
                        budget_remaining=trace.budget_remaining,
                        egress=trace.egress,
                    )
                )

            for rank, result in enumerate(response.results):
                if not result.url:
                    raise ValueError("normalized search results require a URL")
                content_hash = hashlib.sha256(
                    result.url.strip().encode("utf-8")
                ).hexdigest()
                self._ensure_content_identity(
                    session, content_hash, result.url.strip(), now
                )
                result_id = uuid.uuid4().hex
                provider = result.provider.value if result.provider else ""
                session.add(
                    NormalizedResultRow(
                        id=result_id,
                        run_id=ledger_run_id,
                        content_hash=content_hash,
                        url=result.url,
                        title=result.title,
                        snippet=result.snippet,
                        domain=result.domain,
                        provider=provider,
                        score=result.score,
                        final_rank=rank,
                    )
                )
                metadata = dict(result.metadata)
                session.add(
                    ResultProvenanceRow(
                        id=uuid.uuid4().hex,
                        result_id=result_id,
                        provider=provider,
                        egress=metadata.get("egress"),
                        machine=metadata.get("machine"),
                        source_type=metadata.get("source_type", "search"),
                        metadata_json=json.dumps(metadata, sort_keys=True, default=str),
                    )
                )

            delivery_id = uuid.uuid4().hex
            session.add(
                DeliveryIntentRow(
                    id=delivery_id,
                    run_id=ledger_run_id,
                    destination="maya",
                    status="pending",
                    payload_json=json.dumps(
                        {"search_run_id": run_id, "result_count": len(response.results)},
                        sort_keys=True,
                    ),
                    created_at=now,
                )
            )

        return AcceptanceReceipt(run_id=run_id, delivery_intent_id=delivery_id)

    def _existing_receipt(
        self,
        run_id: str | None,
        fingerprint: str,
    ) -> AcceptanceReceipt | None:
        if not run_id:
            return None
        with self.session_factory() as session:
            row = session.scalar(
                select(RetrievalRunRow)
                .where(RetrievalRunRow.search_run_id == run_id)
            )
            if row is None:
                return None
            return self._receipt_for_existing(
                session,
                row,
                fingerprint,
            )

    @staticmethod
    def _receipt_for_existing(
        session,
        row: RetrievalRunRow,
        fingerprint: str,
    ) -> AcceptanceReceipt:
        if row.acceptance_fingerprint != fingerprint:
            raise AcceptanceConflictError(
                f"retrieval {row.search_run_id!r} has a different durable payload"
            )
        delivery_id = session.scalar(
            select(DeliveryIntentRow.id).where(
                DeliveryIntentRow.run_id == row.id
            )
        )
        if delivery_id is None:
            raise AcceptanceConflictError(
                f"retrieval {row.search_run_id!r} is incomplete"
            )
        return AcceptanceReceipt(
            run_id=row.search_run_id,
            delivery_intent_id=delivery_id,
        )

    def load_acceptance_snapshot(
        self,
        search_run_id: str,
    ) -> DurableAcceptanceSnapshot | None:
        """Read the complete durable state for reconciliation."""
        with self.session_factory() as session:
            pair = session.execute(
                select(RetrievalRequestRow, RetrievalRunRow)
                .join(
                    RetrievalRunRow,
                    RetrievalRunRow.request_id == RetrievalRequestRow.id,
                )
                .where(RetrievalRunRow.search_run_id == search_run_id)
            ).one_or_none()
            if pair is None:
                return None
            request, run = pair

            attempts = [
                {
                    "provider": row.provider,
                    "status": row.status,
                    "results_count": row.results_count,
                    "latency_ms": row.latency_ms,
                    "error": row.error,
                    "budget_remaining": row.budget_remaining,
                    "egress": row.egress,
                }
                for row in session.scalars(
                    select(ProviderAttemptRow).where(
                        ProviderAttemptRow.run_id == run.id
                    )
                )
            ]
            attempts.sort(key=_canonical_json)

            result_rows = session.execute(
                select(
                    NormalizedResultRow,
                    ContentIdentityRow,
                    ResultProvenanceRow,
                )
                .outerjoin(
                    ContentIdentityRow,
                    ContentIdentityRow.content_hash
                    == NormalizedResultRow.content_hash,
                )
                .outerjoin(
                    ResultProvenanceRow,
                    ResultProvenanceRow.result_id == NormalizedResultRow.id,
                )
                .where(NormalizedResultRow.run_id == run.id)
                .order_by(NormalizedResultRow.final_rank)
            ).all()
            results = []
            for normalized, identity, provenance in result_rows:
                results.append(
                    {
                        "normalized": {
                            "url": normalized.url,
                            "title": normalized.title,
                            "snippet": normalized.snippet,
                            "domain": normalized.domain,
                            "provider": normalized.provider,
                            "score": normalized.score,
                            "final_rank": normalized.final_rank,
                        },
                        "content_identity": (
                            {
                                "content_hash": identity.content_hash,
                                "canonical_url": identity.canonical_url,
                            }
                            if identity is not None
                            else None
                        ),
                        "provenance": (
                            {
                                "provider": provenance.provider,
                                "egress": provenance.egress,
                                "machine": provenance.machine,
                                "source_type": provenance.source_type,
                                "metadata": _parse_json_value(
                                    provenance.metadata_json
                                ),
                            }
                            if provenance is not None
                            else None
                        ),
                    }
                )

            delivery = session.scalar(
                select(DeliveryIntentRow).where(
                    DeliveryIntentRow.run_id == run.id
                )
            )
            state = {
                "request": {
                    "query_text": request.query_text,
                    "mode": request.mode,
                    "max_results": request.max_results,
                    "caller": request.caller,
                },
                "run": {
                    "search_run_id": run.search_run_id,
                    "status": run.status,
                    "total_results": run.total_results,
                    "cached": run.cached,
                },
                "attempts": attempts,
                "results": results,
                "delivery_intent": (
                    {
                        "destination": delivery.destination,
                        "status": delivery.status,
                        "payload": _parse_json_value(delivery.payload_json),
                    }
                    if delivery is not None
                    else None
                ),
            }
            return DurableAcceptanceSnapshot(
                stored_fingerprint=run.acceptance_fingerprint,
                state=state,
            )

    @staticmethod
    def _ensure_content_identity(session, content_hash, url, created_at):
        values = {
            "content_hash": content_hash,
            "canonical_url": url,
            "created_at": created_at,
        }
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert

            statement = insert(ContentIdentityRow).values(**values)
            session.execute(statement.on_conflict_do_nothing(index_elements=["content_hash"]))
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert

            statement = insert(ContentIdentityRow).values(**values)
            session.execute(statement.on_conflict_do_nothing(index_elements=["content_hash"]))
        elif session.get(ContentIdentityRow, content_hash) is None:
            session.add(ContentIdentityRow(**values))


def create_search_ledger_repository(
    db_url: str | None = None,
    *,
    create_schema: bool | None = None,
) -> SqlAlchemySearchLedgerRepository:
    """Build the SQLAlchemy adapter.

    SQLite is the development adapter and creates its local schema. PostgreSQL
    schema changes are applied only through Alembic.
    """
    url = db_url or get_config().db_url
    is_sqlite = url.startswith("sqlite:")
    if is_sqlite and url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, pool_pre_ping=True)
    should_create = is_sqlite if create_schema is None else create_schema
    if should_create:
        if not is_sqlite:
            raise ValueError("runtime schema creation is only supported for SQLite")
        LedgerBase.metadata.create_all(engine)
    return SqlAlchemySearchLedgerRepository(sessionmaker(bind=engine, expire_on_commit=False))
