"""Normalized, transactionally accepted retrieval-evidence storage.

This is deliberately a persistence seam only.  S7/P1 will wire it into the
broker; importing it registers the SQLite development schema with the legacy
ledger metadata without activating the new retrieval path.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

from argus.broker.accepted import AcceptanceReceipt, AcceptedRetrieval
from argus.persistence.search_ledger import LedgerBase, _system_now


class RetrievalEvidencePlanRow(LedgerBase):
    __tablename__ = "retrieval_evidence_plans"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    cache_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_cohort: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RetrievalEvidenceBatchRow(LedgerBase):
    __tablename__ = "retrieval_evidence_provider_batches"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("retrieval_evidence_plans.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_json: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("plan_id", "provider", "ordinal", name="uq_retrieval_evidence_batch"),)


class RetrievalEvidenceAttemptRow(LedgerBase):
    __tablename__ = "retrieval_evidence_provider_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("retrieval_evidence_provider_batches.id"), nullable=False)
    attempt_ref: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_json: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("batch_id", "ordinal", name="uq_retrieval_evidence_attempt"),)


class RetrievalEvidenceObservationRow(LedgerBase):
    __tablename__ = "retrieval_evidence_observations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("retrieval_evidence_plans.id"), nullable=False)
    observation_ref: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    observation_json: Mapped[str] = mapped_column(Text, nullable=False)


class RetrievalEvidenceClusterRow(LedgerBase):
    __tablename__ = "retrieval_evidence_clusters"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("retrieval_evidence_plans.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    cluster_json: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("plan_id", "ordinal", name="uq_retrieval_evidence_cluster"),)


class RetrievalEvidenceContributionRow(LedgerBase):
    __tablename__ = "retrieval_evidence_contributions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("retrieval_evidence_clusters.id"), nullable=False)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("retrieval_evidence_provider_attempts.id"), nullable=False)
    contribution_json: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("cluster_id", "attempt_id", name="uq_retrieval_evidence_contribution"),)


class RetrievalEvidenceReadinessRow(LedgerBase):
    __tablename__ = "retrieval_evidence_readiness_decisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("retrieval_evidence_plans.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_json: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("plan_id", "provider", name="uq_retrieval_evidence_readiness"),)


class RetrievalEvidenceCacheLineageRow(LedgerBase):
    __tablename__ = "retrieval_evidence_cache_lineage"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("retrieval_evidence_plans.id"), nullable=False, unique=True)
    cache_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_cohort: Mapped[str] = mapped_column(String(128), nullable=False)
    lineage_json: Mapped[str] = mapped_column(Text, nullable=False)


class RetrievalEvidenceAccountingRow(LedgerBase):
    __tablename__ = "retrieval_evidence_accounting"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("retrieval_evidence_plans.id"), nullable=False, unique=True)
    accounting_json: Mapped[str] = mapped_column(Text, nullable=False)


class RetrievalEvidenceTraceRow(LedgerBase):
    __tablename__ = "retrieval_evidence_trace_refs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("retrieval_evidence_provider_attempts.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    trace_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    __table_args__ = (UniqueConstraint("attempt_id", "ordinal", name="uq_retrieval_evidence_trace"),)


class AcceptedOperationRow(LedgerBase):
    __tablename__ = "accepted_retrieval_operations"
    receipt_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("retrieval_evidence_plans.id"), nullable=False, unique=True)
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    acceptance_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    __table_args__ = (UniqueConstraint("receipt_ref", "plan_id", name="uq_accepted_retrieval_receipt_plan"),)


class RetrievalCachePublicationRow(LedgerBase):
    __tablename__ = "retrieval_cache_publications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    receipt_ref: Mapped[str] = mapped_column(ForeignKey("accepted_retrieval_operations.receipt_ref"), nullable=False, unique=True)
    cache_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_cohort: Mapped[str] = mapped_column(String(128), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    __table_args__ = (UniqueConstraint("cache_fingerprint", "execution_cohort", name="uq_retrieval_cache_publication"),)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class RetrievalEvidence:
    accepted: AcceptedRetrieval

    @classmethod
    def from_accepted(cls, accepted: AcceptedRetrieval) -> "RetrievalEvidence":
        if not accepted.contributor_attempt_refs:
            raise ValueError("accepted retrieval requires contributor lineage")
        return cls(accepted=accepted)

    @property
    def source_fingerprint(self) -> str:
        payload = {
            "operation_id": self.accepted.operation_id,
            "cache_fingerprint": self.accepted.cache_fingerprint,
            "execution_cohort": self.accepted.execution_cohort,
            "outcome": self.accepted.outcome.value,
            "attempts": self.accepted.contributor_attempt_refs,
            "receipt": self.accepted.acceptance_receipt.acceptance_fingerprint,
        }
        return hashlib.sha256(_canonical(payload).encode()).hexdigest()


class SqlAlchemyEvidenceRepository:
    """Commit complete evidence, its receipt, and its publication identity atomically."""

    def __init__(self, session_factory: sessionmaker, *, clock: Callable[[], datetime] = _system_now):
        self.session_factory = session_factory
        self.clock = clock

    def accept(self, evidence: RetrievalEvidence) -> AcceptanceReceipt:
        accepted = evidence.accepted
        try:
            with self.session_factory.begin() as session:
                existing = session.scalar(select(AcceptedOperationRow).where(AcceptedOperationRow.operation_id == accepted.operation_id))
                if existing is not None:
                    if existing.acceptance_fingerprint != accepted.acceptance_receipt.acceptance_fingerprint:
                        raise ValueError("operation already accepted with different evidence")
                    return accepted.acceptance_receipt

                plan_id = uuid.uuid4().hex
                session.add(RetrievalEvidencePlanRow(
                    id=plan_id, operation_id=accepted.operation_id,
                    cache_fingerprint=accepted.cache_fingerprint, execution_cohort=accepted.execution_cohort,
                    plan_json=_canonical({"results": accepted.results}), source_fingerprint=evidence.source_fingerprint,
                    created_at=self.clock(),
                ))
                batch_id = uuid.uuid4().hex
                session.add(RetrievalEvidenceBatchRow(id=batch_id, plan_id=plan_id, provider="accepted", ordinal=0, batch_json="{}"))
                for ordinal, attempt_ref in enumerate(accepted.contributor_attempt_refs):
                    session.add(RetrievalEvidenceAttemptRow(id=uuid.uuid4().hex, batch_id=batch_id, attempt_ref=attempt_ref, ordinal=ordinal, attempt_json="{}"))
                session.add(RetrievalEvidenceCacheLineageRow(
                    id=uuid.uuid4().hex, plan_id=plan_id, cache_fingerprint=accepted.cache_fingerprint,
                    execution_cohort=accepted.execution_cohort, lineage_json=_canonical({"origin_receipt": accepted.acceptance_receipt.receipt_ref}),
                ))
                session.add(RetrievalEvidenceAccountingRow(id=uuid.uuid4().hex, plan_id=plan_id, accounting_json=_canonical({"origin_spend_usd": accepted.origin_spend_usd})))
                session.add(AcceptedOperationRow(
                    receipt_ref=accepted.acceptance_receipt.receipt_ref, plan_id=plan_id,
                    operation_id=accepted.operation_id,
                    acceptance_fingerprint=accepted.acceptance_receipt.acceptance_fingerprint,
                    outcome=accepted.outcome.value, accepted_at=accepted.acceptance_receipt.accepted_at,
                ))
                session.add(RetrievalCachePublicationRow(
                    id=uuid.uuid4().hex, receipt_ref=accepted.acceptance_receipt.receipt_ref,
                    cache_fingerprint=accepted.cache_fingerprint, execution_cohort=accepted.execution_cohort,
                    published_at=accepted.acceptance_receipt.accepted_at,
                ))
            return accepted.acceptance_receipt
        except IntegrityError:
            with self.session_factory() as session:
                existing = session.scalar(select(AcceptedOperationRow).where(AcceptedOperationRow.operation_id == accepted.operation_id))
                if existing is not None and existing.acceptance_fingerprint == accepted.acceptance_receipt.acceptance_fingerprint:
                    return accepted.acceptance_receipt
            raise

    def accepted_count(self) -> int:
        with self.session_factory() as session:
            return len(session.scalars(select(AcceptedOperationRow)).all())

    def publication_count(self) -> int:
        with self.session_factory() as session:
            return len(session.scalars(select(RetrievalCachePublicationRow)).all())
