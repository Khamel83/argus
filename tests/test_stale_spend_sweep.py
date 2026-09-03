from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from argus.broker.readiness import (
    ExecutionContext,
    ProviderObservation,
    ProviderReadinessService,
    ProviderRegistrationSpec,
)
from argus.models import ProviderName
from argus.persistence.provider_spend import ProviderSpendAttemptRow
from argus.persistence.provider_spend import ProviderSpendRepository
from argus.persistence.readiness import (
    ProviderReadinessLeaseRow,
    create_readiness_repository,
)
from argus.providers.fixture_attestation import build_fixture_attestation


def _service(tmp_path):
    repository = create_readiness_repository(
        f"sqlite:///{tmp_path / 'stale-sweep.db'}",
        create_schema=True,
    )
    service = ProviderReadinessService(repository=repository)
    ref, attestation = build_fixture_attestation(
        ProviderName.BRAVE,
        release="argus-1.6.4",
        provider_contract="2026-07-27-v1",
    )
    service.register_provider(
        ProviderRegistrationSpec(
            provider=ProviderName.BRAVE,
            enabled=True,
            configuration_fingerprint="stale-config",
            credential_version_fingerprint="stale-credential",
            account_fingerprint="stale-account",
            budget_limit=10.0,
            durable_spend_repository=True,
            release_revision="argus-1.6.4",
            contract_version="2026-07-27-v1",
            fixture_evidence_ref=ref,
            fixture_attestation=attestation,
        )
    )
    for request_class in ("discovery", "research", "recovery", "grounding"):
        scope = service.execution_scope(
            ProviderName.BRAVE,
            egress="local",
            request_class=request_class,
        )
        service.record_observation(
            ProviderObservation(
                provider=ProviderName.BRAVE,
                dimension="reachability",
                state="reachable",
                source="stale-test",
                scope=scope,
                observed_at=repository.authority_now(),
                ttl_seconds=60,
            )
        )
        service.record_observation(
            ProviderObservation(
                provider=ProviderName.BRAVE,
                dimension="usability",
                state="usable",
                source="stale-test",
                scope=scope,
                observed_at=repository.authority_now(),
                ttl_seconds=60,
            )
        )
    return service, repository


def _authorize(service, repository, *, account="stale-account"):
    scope = service.execution_scope(
        ProviderName.BRAVE,
        egress="local",
        request_class="discovery",
    )
    from dataclasses import replace

    scope = replace(scope, account_fingerprint=account)
    for dimension, state in (("reachability", "reachable"), ("usability", "usable")):
        service.record_observation(
            ProviderObservation(
                provider=ProviderName.BRAVE,
                dimension=dimension,
                state=state,
                source="stale-test",
                scope=scope,
                observed_at=repository.authority_now(),
                ttl_seconds=60,
            )
        )
    context = ExecutionContext(
        provider=ProviderName.BRAVE,
        tier=1,
        plan_providers=(ProviderName.BRAVE,),
        free_only=False,
        caller_tier_cap=None,
        scope=scope,
        plan_id="stale-plan",
        caller_identity="stale-caller",
        caller_label="stale-test",
        idempotency_key=f"stale-operation:{account}",
        egress="local",
        request_class="discovery",
        release_revision="argus-1.6.4",
        contract_version="2026-07-27-v1",
        operation_id=f"stale-operation:{account}",
        request_hash="a" * 64,
        release_identity="release-stale",
    )
    return service.authorize_execution(
        context,
        owner=f"stale-owner:{account}",
        conservative_charge=1.0,
        execution_timeout_seconds=30,
    )


def test_stale_sweep_transitions_expired_reservation_once_and_records_evidence(tmp_path):
    service, repository = _service(tmp_path)
    spend = ProviderSpendRepository(repository.session_factory)
    authorization = _authorize(service, repository)
    repository.advance_authority_clock_for_test(timedelta(seconds=31))

    swept = repository.list_stale_execution_attempts(limit=10)

    assert [attempt.attempt_id for attempt in swept] == [authorization.attempt_id]
    attempt = spend.get_attempt(authorization.attempt_id)
    assert attempt.status == "uncertain"
    assert attempt.outcome == "execution_deadline_expired"
    with repository.session_factory() as session:
        lease = session.scalar(
            select(ProviderReadinessLeaseRow).where(
                ProviderReadinessLeaseRow.attempt_id == authorization.attempt_id
            )
        )
        assert lease.state == "unresolved"
        assert lease.uncertain_charge is True
        audit = session.scalar(
            select(ProviderSpendAttemptRow).where(
                ProviderSpendAttemptRow.id == authorization.attempt_id
            )
        )
        assert audit.account_fingerprint == "stale-account"

    assert repository.list_stale_execution_attempts(limit=10) == ()
    assert service.snapshot(ProviderName.BRAVE).spend == "uncertain"


def test_stale_sweep_is_bounded_and_does_not_touch_active_rows(tmp_path):
    service, repository = _service(tmp_path)
    first = _authorize(service, repository, account="stale-account")
    repository.advance_authority_clock_for_test(timedelta(seconds=31))

    swept = repository.list_stale_execution_attempts(limit=1)

    assert len(swept) == 1
    assert swept[0].attempt_id == first.attempt_id
    spend = ProviderSpendRepository(repository.session_factory)
    assert spend.get_attempt(first.attempt_id).status == "uncertain"
    assert repository.list_stale_execution_attempts(limit=1) == ()
