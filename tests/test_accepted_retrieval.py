"""S6 cache admission and durable-acceptance contracts.

Each test protects a concrete retrieval corruption failure: reusing evidence
outside its policy identity, mutating a published fact, or publishing before
the one durable acceptance transaction commits.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest

from argus.broker.accepted import (
    AcceptanceReceipt,
    AcceptedRetrieval,
    CacheEntry,
    CacheOutcome,
    RetrievalCache,
    canonical_cache_outcome,
)
from argus.contracts.outcomes import CanonicalOutcome
from argus.persistence.evidence import RetrievalEvidence, SqlAlchemyEvidenceRepository
from argus.persistence.search_ledger import create_search_ledger_repository


NOW = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)


def _accepted(
    *,
    outcome: CacheOutcome = CacheOutcome.SUCCESS,
    operation_id: str = "operation-origin",
    cache_fingerprint: str = "cache-fingerprint",
    execution_cohort: str = "free:en",
    contributor_attempt_refs: tuple[str, ...] = (
        "attempt-brave",
        "attempt-duckduckgo",
    ),
    receipt_ref: str = "receipt-origin",
    acceptance_fingerprint: str = "a" * 64,
) -> AcceptedRetrieval:
    return AcceptedRetrieval(
        operation_id=operation_id,
        cache_fingerprint=cache_fingerprint,
        execution_cohort=execution_cohort,
        outcome=outcome,
        results=(
            {
                "url": "https://example.com/answer",
                "title": "answer",
                "providers": ("brave", "duckduckgo"),
            },
        ),
        contributor_attempt_refs=contributor_attempt_refs,
        origin_spend_usd="0.01",
        acceptance_receipt=AcceptanceReceipt(
            receipt_ref=receipt_ref,
            accepted_at=NOW,
            acceptance_fingerprint=acceptance_fingerprint,
        ),
    )


def _entry(
    *,
    accepted_at: datetime = NOW,
    outcome: CacheOutcome = CacheOutcome.SUCCESS,
) -> CacheEntry:
    accepted = _accepted(outcome=outcome)
    return CacheEntry.from_accepted(accepted, accepted_at=accepted_at)


def test_free_profile_reuses_eligible_paid_origin_without_new_calls_or_spend():
    """Removing profile-compatible reuse would re-run paid providers needlessly."""
    cache = RetrievalCache(clock=lambda: NOW)
    cache.publish(_entry())

    decision = cache.decide(
        cache_fingerprint="cache-fingerprint",
        execution_cohort="free:en",
        max_age_seconds=60,
    )

    assert decision.outcome.value == "hit_eligible"
    assert decision.accepted is not None
    assert decision.accepted.origin_spend_usd == "0.01"
    assert decision.accepted.current_spend_usd == "0"
    assert decision.accepted.current_provider_calls == 0
    assert decision.accepted.contributor_attempt_refs == (
        "attempt-brave",
        "attempt-duckduckgo",
    )


@pytest.mark.parametrize(
    ("fingerprint", "cohort", "max_age_seconds", "now"),
    [
        ("different-fingerprint", "free:en", 60, NOW),
        ("cache-fingerprint", "free:fr", 60, NOW),
        ("cache-fingerprint", "free:en", 59, NOW + timedelta(seconds=60)),
    ],
)
def test_cache_admission_rejects_identity_or_freshness_mismatch(
    fingerprint: str,
    cohort: str,
    max_age_seconds: int,
    now: datetime,
):
    """Ignoring any identity component would leak a stale or wrong-policy result."""
    cache = RetrievalCache(clock=lambda: now)
    cache.publish(_entry())

    decision = cache.decide(
        cache_fingerprint=fingerprint,
        execution_cohort=cohort,
        max_age_seconds=max_age_seconds,
    )

    assert decision.outcome.value == "hit_ineligible"
    assert decision.accepted is None


def test_proven_empty_is_short_lived_and_cache_hits_are_immutable_copies():
    """A mutation or stale-empty fallback would corrupt later retrieval decisions."""
    fresh_cache = RetrievalCache(clock=lambda: NOW)
    fresh_cache.publish(_entry(outcome=CacheOutcome.PROVEN_EMPTY))
    first = fresh_cache.decide(
        cache_fingerprint="cache-fingerprint",
        execution_cohort="free:en",
        max_age_seconds=60,
    )
    assert first.outcome.value == "hit_eligible"
    assert first.accepted is not None
    first.accepted.results[0]["title"] = "mutated by caller"

    second = fresh_cache.decide(
        cache_fingerprint="cache-fingerprint",
        execution_cohort="free:en",
        max_age_seconds=60,
    )
    assert second.accepted is not None
    assert second.accepted.results[0]["title"] == "answer"

    stale_cache = RetrievalCache(clock=lambda: NOW + timedelta(seconds=61))
    stale_cache.publish(_entry(outcome=CacheOutcome.PROVEN_EMPTY))
    stale = stale_cache.decide(
        cache_fingerprint="cache-fingerprint",
        execution_cohort="free:en",
        max_age_seconds=60,
    )
    assert stale.outcome.value == "hit_ineligible"
    assert stale.accepted is None


def test_cache_publication_happens_only_after_durable_acceptance():
    """Publishing before commit would expose an entry without a receipt."""
    events: list[str] = []
    cache = RetrievalCache(clock=lambda: NOW, on_publish=lambda: events.append("cache"))

    with pytest.raises(RuntimeError, match="persistence failed"):
        cache.accept_and_publish(
            _accepted(),
            persist=lambda accepted: (_ for _ in ()).throw(
                RuntimeError("persistence failed")
            ),
        )

    assert events == []
    assert cache.size() == 0

    receipt = cache.accept_and_publish(
        _accepted(),
        persist=lambda accepted: (
            events.append("durable") or accepted.acceptance_receipt
        ),
    )
    assert receipt.receipt_ref == "receipt-origin"
    assert events == ["durable", "cache"]


@pytest.mark.parametrize(
    ("outcome", "reason", "expected"),
    [
        (CanonicalOutcome.SUCCESS, "accepted", CacheOutcome.SUCCESS),
        (CanonicalOutcome.DEGRADED, "partial_provider_failure", CacheOutcome.DEGRADED),
        (CanonicalOutcome.EMPTY, "strict_empty", CacheOutcome.PROVEN_EMPTY),
        (CanonicalOutcome.EMPTY, "freshness_unproven", CacheOutcome.FRESHNESS_UNPROVEN),
        (
            CanonicalOutcome.EMPTY,
            "research_structural_floor_unmet",
            CacheOutcome.STRUCTURAL_FLOOR_FAILURE,
        ),
        (
            CanonicalOutcome.PROVIDERS_FAILED,
            "providers_failed",
            CacheOutcome.EVERY_PROVIDER_FAILURE,
        ),
        (CanonicalOutcome.TIMEOUT, "operation_deadline", CacheOutcome.TIMEOUT),
        (CanonicalOutcome.UNREADY, "no_reachable_provider", CacheOutcome.UNREADY),
        (
            CanonicalOutcome.PERSISTENCE_FAILED,
            "write_failed",
            CacheOutcome.PERSISTENCE_FAILURE,
        ),
    ],
)
def test_outcome_selection_uses_complete_fusion_trace_not_result_count(
    outcome, reason, expected
):
    """Classifying zero results alone would turn failures into fabricated empties."""
    assert canonical_cache_outcome(outcome, reason=reason) is expected


def test_durable_acceptance_is_idempotent_and_precedes_cache_publication(tmp_path):
    """Duplicating a leader must not create another receipt or cache fact."""
    ledger = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'evidence.db'}", create_schema=True, clock=lambda: NOW
    )
    evidence = SqlAlchemyEvidenceRepository(ledger.session_factory, clock=lambda: NOW)
    accepted = _accepted()

    first = evidence.accept(RetrievalEvidence.from_accepted(accepted))
    second = evidence.accept(RetrievalEvidence.from_accepted(accepted))

    assert first == second == accepted.acceptance_receipt
    assert evidence.accepted_count() == 1
    assert evidence.publication_count() == 1


def test_postgresql_concurrent_acceptance_is_single_publish_and_atomic(
    postgres_ledger_url,
):
    """Racing leaders must publish once and roll back every losing evidence row."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError

    from argus.persistence.evidence import RetrievalEvidencePlanRow

    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        postgres_ledger_url.replace("%", "%%"),
    )
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    ledgers = [
        create_search_ledger_repository(
            postgres_ledger_url,
            create_schema=False,
        )
        for _ in range(2)
    ]
    repositories = [
        SqlAlchemyEvidenceRepository(
            ledger.session_factory,
            clock=lambda: NOW,
        )
        for ledger in ledgers
    ]

    identical = RetrievalEvidence.from_accepted(
        _accepted(
            operation_id="concurrent-identical",
            cache_fingerprint="concurrent-identical-cache",
            contributor_attempt_refs=(
                "concurrent-identical-brave",
                "concurrent-identical-duckduckgo",
            ),
            receipt_ref="concurrent-identical-receipt",
            acceptance_fingerprint="b" * 64,
        )
    )
    identical_barrier = Barrier(2)

    def accept_identical(index):
        identical_barrier.wait()
        return repositories[index].accept(identical)

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(accept_identical, range(2)))

    assert receipts == [
        identical.accepted.acceptance_receipt,
        identical.accepted.acceptance_receipt,
    ]
    assert repositories[0].accepted_count() == 1
    assert repositories[0].publication_count() == 1

    collision = [
        RetrievalEvidence.from_accepted(
            _accepted(
                operation_id=f"concurrent-collision-{index}",
                cache_fingerprint="concurrent-collision-cache",
                contributor_attempt_refs=(
                    f"concurrent-collision-{index}-brave",
                    f"concurrent-collision-{index}-duckduckgo",
                ),
                receipt_ref=f"concurrent-collision-receipt-{index}",
                acceptance_fingerprint=str(index + 1) * 64,
            )
        )
        for index in range(2)
    ]
    collision_barrier = Barrier(2)

    def accept_collision(index):
        collision_barrier.wait()
        try:
            return repositories[index].accept(collision[index])
        except Exception as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(accept_collision, range(2)))

    assert sum(isinstance(outcome, AcceptanceReceipt) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, IntegrityError) for outcome in outcomes) == 1
    assert repositories[0].accepted_count() == 2
    assert repositories[0].publication_count() == 2
    with ledgers[0].session_factory() as session:
        plan_count = session.scalar(
            select(func.count()).select_from(RetrievalEvidencePlanRow)
        )
        collision_operations = set(
            session.scalars(
                select(RetrievalEvidencePlanRow.operation_id).where(
                    RetrievalEvidencePlanRow.operation_id.like("concurrent-collision-%")
                )
            )
        )
    assert plan_count == 2
    assert len(collision_operations) == 1


def test_0009_downgrade_refuses_after_an_accepted_receipt(monkeypatch):
    """Dropping accepted evidence would destroy a published immutable fact."""
    migration = import_module("migrations.versions.0009_retrieval_evidence")
    dropped: list[str] = []

    class Bind:
        def execute(self, statement):
            assert "accepted_retrieval_operations" in str(statement)
            return type("Result", (), {"scalar_one": lambda self: 1})()

    monkeypatch.setattr(migration.op, "get_bind", lambda: Bind())
    monkeypatch.setattr(migration.op, "drop_table", lambda table: dropped.append(table))

    with pytest.raises(RuntimeError, match="accepted retrieval evidence exists"):
        migration.downgrade()
    assert dropped == []
