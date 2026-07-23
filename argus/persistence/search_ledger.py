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
        try:
            return self._accept_once(query, response)
        except IntegrityError:
            # A concurrent request with the same public run ID may win the
            # unique-key race after our initial lookup. Acknowledge only after
            # its complete transaction, including delivery intent, is visible.
            for _ in range(100):
                receipt = self._existing_receipt(response.search_run_id)
                if receipt is not None:
                    return receipt
                time.sleep(0.01)
            raise

    def _accept_once(
        self, query: SearchQuery, response: SearchResponse
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
                delivery_id = session.scalar(
                    select(DeliveryIntentRow.id).where(
                        DeliveryIntentRow.run_id == existing.id
                    )
                )
                if delivery_id is None:
                    raise RuntimeError(
                        f"retrieval {run_id!r} exists without delivery intent"
                    )
                return AcceptanceReceipt(
                    run_id=existing.search_run_id,
                    delivery_intent_id=delivery_id,
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

    def _existing_receipt(self, run_id: str | None) -> AcceptanceReceipt | None:
        if not run_id:
            return None
        with self.session_factory() as session:
            row = session.execute(
                select(RetrievalRunRow.search_run_id, DeliveryIntentRow.id)
                .join(
                    DeliveryIntentRow,
                    DeliveryIntentRow.run_id == RetrievalRunRow.id,
                )
                .where(RetrievalRunRow.search_run_id == run_id)
            ).one_or_none()
            if row is None:
                return None
            return AcceptanceReceipt(
                run_id=row.search_run_id,
                delivery_intent_id=row.id,
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
