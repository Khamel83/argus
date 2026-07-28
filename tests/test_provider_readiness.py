from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

import pytest

from argus.models import ProviderName


UTC = timezone.utc


def _service(tmp_path):
    from argus.broker.readiness import ProviderReadinessService
    from argus.persistence.readiness import create_readiness_repository

    repository = create_readiness_repository(
        f"sqlite:///{tmp_path / 'readiness.db'}"
    )
    return ProviderReadinessService(repository=repository), repository


def _observation(
    provider: ProviderName,
    dimension: str,
    state: str,
    *,
    receipt: str,
    ttl_seconds: int | None = 300,
    egress: str = "local",
    request_class: str = "discovery",
    protected: bool = False,
):
    from argus.broker.readiness import ProviderObservation, ReadinessScope

    return ProviderObservation(
        provider=provider,
        dimension=dimension,
        state=state,
        source="test_fixture",
        scope=ReadinessScope(
            egress=egress,
            machine="test",
            request_class=request_class,
            release_revision="release-1",
            contract_version="contract-1",
            configuration_fingerprint="config:v1",
            credential_version_fingerprint="secret-version:v1",
            account_fingerprint="account:v1",
        ),
        observed_at=datetime.now(UTC),
        ttl_seconds=ttl_seconds,
        evidence_ref=receipt,
        protected=protected,
    )


def _ready(service, provider=ProviderName.BRAVE):
    for dimension, state in (
        ("registration", "registered"),
        ("configuration", "configured"),
        ("compatibility", "compatible"),
        ("reachability", "reachable"),
        ("usability", "usable"),
        ("cooldown", "clear"),
        ("spend", "available"),
    ):
        service.record_observation(
            _observation(provider, dimension, state, receipt=f"{dimension}-1")
        )


def test_snapshot_is_immutable_and_derives_healthy_from_orthogonal_evidence(tmp_path):
    service, _ = _service(tmp_path)
    _ready(service)

    snapshot = service.snapshot(
        ProviderName.BRAVE,
        egress="local",
        request_class="discovery",
        release_revision="release-1",
        contract_version="contract-1",
    )

    assert snapshot.catalog_status == "supported"
    assert snapshot.registration == "registered"
    assert snapshot.configuration.configured is True
    assert snapshot.configuration.issues == ()
    assert snapshot.healthy is True
    assert snapshot.execution_decision.code == "eligible"
    assert snapshot.valid_until is not None
    with pytest.raises(FrozenInstanceError):
        snapshot.healthy = False


@pytest.mark.parametrize(
    ("dimension", "state", "code"),
    [
        ("registration", "not_registered", "unavailable"),
        ("configuration", "missing_credential", "unavailable"),
        ("compatibility", "unknown", "compatibility_unproven"),
        ("reachability", "unreachable", "unavailable"),
        ("cooldown", "active", "cooldown"),
        ("spend", "exhausted", "spend_blocked"),
        ("spend", "uncertain", "spend_blocked"),
    ],
)
def test_deterministic_fail_closed_decision_fold(tmp_path, dimension, state, code):
    service, _ = _service(tmp_path)
    _ready(service)
    service.record_observation(
        _observation(ProviderName.BRAVE, dimension, state, receipt="blocking")
    )

    snapshot = service.snapshot(
        ProviderName.BRAVE,
        egress="local",
        request_class="discovery",
        release_revision="release-1",
        contract_version="contract-1",
    )

    assert snapshot.execution_decision.code == code
    assert snapshot.healthy is False


def test_policy_precedes_other_failures_and_free_only_is_rechecked(tmp_path):
    from argus.broker.readiness import ExecutionContext

    service, _ = _service(tmp_path)
    _ready(service)

    decision = service.execution_decision(
        ExecutionContext(
            provider=ProviderName.BRAVE,
            tier=1,
            plan_providers=(ProviderName.BRAVE,),
            free_only=True,
            caller_tier_cap=3,
            egress="local",
            request_class="discovery",
            release_revision="release-1",
            contract_version="contract-1",
        )
    )

    assert decision.code == "policy_skipped"
    assert decision.reason == "free_only"


def test_authority_clock_rejects_future_skew_and_expires_to_unknown(tmp_path):
    service, repository = _service(tmp_path)
    future = _observation(
        ProviderName.BRAVE, "reachability", "reachable", receipt="future"
    )
    object.__setattr__(
        future, "observed_at", datetime.now(UTC) + timedelta(seconds=31)
    )
    with pytest.raises(ValueError, match="future"):
        service.record_observation(future)

    service.record_observation(
        _observation(
            ProviderName.BRAVE,
            "reachability",
            "reachable",
            receipt="short",
            ttl_seconds=1,
        )
    )
    repository.advance_authority_clock_for_test(timedelta(seconds=2))
    snapshot = service.snapshot(
        ProviderName.BRAVE, egress="local", request_class="discovery"
    )
    assert snapshot.reachability == "unknown"


def test_valid_until_uses_earliest_expiry_and_renderer_cache_never_outlives_it(
    tmp_path,
):
    service, repository = _service(tmp_path)
    service.record_observation(
        _observation(
            ProviderName.DUCKDUCKGO,
            "compatibility",
            "compatible",
            receipt="long",
            ttl_seconds=20,
        )
    )
    service.record_observation(
        _observation(
            ProviderName.DUCKDUCKGO,
            "reachability",
            "reachable",
            receipt="short",
            ttl_seconds=3,
        )
    )
    first = service.render_snapshot(
        ProviderName.DUCKDUCKGO, egress="local", request_class="discovery"
    )
    repository.advance_authority_clock_for_test(timedelta(seconds=4))
    second = service.render_snapshot(
        ProviderName.DUCKDUCKGO, egress="local", request_class="discovery"
    )
    assert first.valid_until < first.observed_at + timedelta(seconds=4)
    assert second.generation > first.generation
    assert second.reachability == "unknown"


def test_receipts_compact_to_32_and_protected_overflow_fails_closed(tmp_path):
    service, _ = _service(tmp_path)
    for index in range(40):
        service.record_observation(
            _observation(
                ProviderName.BRAVE,
                "usability",
                "usable",
                receipt=f"diagnostic-{index:02}",
            )
        )
    snapshot = service.snapshot(
        ProviderName.BRAVE, egress="local", request_class="discovery"
    )
    assert len(snapshot.evidence_receipts) == 32
    assert "diagnostic-39" in snapshot.evidence_receipts

    for index in range(33):
        service.record_observation(
            _observation(
                ProviderName.SERPER,
                "spend",
                "exhausted",
                receipt=f"terminal-{index:02}",
                ttl_seconds=None,
                protected=True,
            )
        )
    overflow = service.snapshot(
        ProviderName.SERPER, egress="local", request_class="discovery"
    )
    assert overflow.execution_decision.code == "unavailable"
    assert overflow.execution_decision.reason == "evidence_overflow"
    assert overflow.protected_evidence_count == 33


def test_scope_manifest_is_bounded_to_32_executable_scopes(tmp_path):
    service, _ = _service(tmp_path)
    service.validate_scope_manifest(
        egresses=tuple(f"egress-{index}" for index in range(8)),
        request_classes=("discovery", "recovery", "grounding", "research"),
    )
    with pytest.raises(ValueError, match="32"):
        service.validate_scope_manifest(
            egresses=tuple(f"egress-{index}" for index in range(9)),
            request_classes=("discovery", "recovery", "grounding", "research"),
        )


def test_probe_authorization_is_closed_and_routine_diagnostics_are_no_spend(tmp_path):
    from argus.broker.readiness import ProbeAuthorization

    service, _ = _service(tmp_path)
    assert service.authorize_probe(ProviderName.BRAVE, "fixture").allowed
    assert service.authorize_probe(ProviderName.BRAVE, "local_component").allowed
    assert not service.authorize_probe(ProviderName.BRAVE, "billable_search").allowed
    assert not service.authorize_probe(ProviderName.BRAVE, "unknown").allowed
    assert not service.authorize_probe(
        ProviderName.BRAVE, "no_spend_account"
    ).allowed
    allowed = service.authorize_probe(
        ProviderName.BRAVE,
        "no_spend_account",
        ProbeAuthorization(
            workflow="diagnostic",
            provider=ProviderName.BRAVE,
            allowlist_version="2026-07-27",
            endpoint_contract="brave-account-v1",
        ),
    )
    assert allowed.allowed


def test_two_repositories_grant_one_half_open_claim_and_fence_stale_completion(
    tmp_path,
):
    from argus.persistence.readiness import (
        StaleFencingToken,
        create_readiness_repository,
    )

    url = f"sqlite:///{tmp_path / 'concurrency.db'}"
    first = create_readiness_repository(url)
    second = create_readiness_repository(url)
    barrier = Barrier(2)
    results = []

    def claim(repository, owner):
        barrier.wait()
        results.append(
            repository.claim_half_open(
                scope_key="brave:account:v1:local:discovery",
                owner=owner,
                execution_timeout_seconds=30,
                attempt_id=f"attempt-{owner}",
            )
        )

    threads = [
        Thread(target=claim, args=(first, "one")),
        Thread(target=claim, args=(second, "two")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    claims = [claim for claim in results if claim is not None]
    assert len(claims) == 1
    winner = claims[0]
    first.advance_authority_clock_for_test(timedelta(seconds=31))
    assert (
        second.claim_half_open(
            scope_key=winner.scope_key,
            owner="replacement",
            execution_timeout_seconds=30,
            attempt_id="attempt-replacement",
        )
        is None
    )
    first.complete_half_open(
        scope_key=winner.scope_key,
        owner=winner.owner,
        fencing_token=winner.fencing_token,
        outcome="timeout",
        uncertain_charge=False,
    )
    replacement = second.claim_half_open(
        scope_key=winner.scope_key,
        owner="replacement",
        execution_timeout_seconds=30,
        attempt_id="attempt-replacement",
    )
    assert replacement is not None
    assert replacement.fencing_token > winner.fencing_token
    with pytest.raises(StaleFencingToken):
        first.complete_half_open(
            scope_key=winner.scope_key,
            owner=winner.owner,
            fencing_token=winner.fencing_token,
            outcome="success",
            uncertain_charge=False,
        )


def test_completion_is_idempotent_and_terminal_exhaustion_survives_restart(tmp_path):
    from argus.persistence.readiness import create_readiness_repository

    url = f"sqlite:///{tmp_path / 'terminal.db'}"
    repository = create_readiness_repository(url)
    claim = repository.claim_half_open(
        scope_key="serper:account:v1:local:discovery",
        owner="worker",
        execution_timeout_seconds=30,
        attempt_id="attempt-1",
    )
    assert claim is not None
    first = repository.complete_half_open(
        scope_key=claim.scope_key,
        owner=claim.owner,
        fencing_token=claim.fencing_token,
        outcome="balance_exhausted",
        uncertain_charge=False,
    )
    replay = repository.complete_half_open(
        scope_key=claim.scope_key,
        owner=claim.owner,
        fencing_token=claim.fencing_token,
        outcome="balance_exhausted",
        uncertain_charge=False,
    )
    assert replay == first
    repository.record_terminal_exhaustion(
        provider=ProviderName.SERPER,
        account_fingerprint="account:v1",
        recurring=False,
        reset_at=None,
        evidence_ref="receipt-exhausted",
    )
    restarted = create_readiness_repository(url)
    assert restarted.spend_state(
        ProviderName.SERPER, account_fingerprint="account:v1"
    ) == "exhausted"
    assert restarted.emit_operator_alert_once(
        ProviderName.SERPER,
        account_fingerprint="account:v1",
        alert_kind="exhaustion_without_refresh",
    )
    assert not restarted.emit_operator_alert_once(
        ProviderName.SERPER,
        account_fingerprint="account:v1",
        alert_kind="exhaustion_without_refresh",
    )


def test_recurring_exhaustion_resets_only_at_documented_boundary(tmp_path):
    service, repository = _service(tmp_path)
    now = repository.authority_now()
    repository.record_terminal_exhaustion(
        provider=ProviderName.BRAVE,
        account_fingerprint="account:v1",
        recurring=True,
        reset_at=now + timedelta(hours=1),
        evidence_ref="monthly-reset",
    )
    repository.advance_authority_clock_for_test(timedelta(minutes=59))
    assert repository.spend_state(
        ProviderName.BRAVE, account_fingerprint="account:v1"
    ) == "exhausted"
    repository.advance_authority_clock_for_test(timedelta(minutes=2))
    assert repository.spend_state(
        ProviderName.BRAVE, account_fingerprint="account:v1"
    ) == "unknown"


def test_catalog_contains_all_14_real_providers_and_registration_is_explicit(tmp_path):
    from argus.broker.readiness import RegistrationInputs, provider_catalog

    service, _ = _service(tmp_path)
    assert len(provider_catalog()) == 14
    registration = service.evaluate_registration(
        ProviderName.BRAVE,
        RegistrationInputs(
            enabled=True,
            credential_present=True,
            account_fingerprint=None,
            budget_limit=None,
            durable_spend_repository=False,
            compatible_fixture_release=False,
        ),
    )
    assert registration.registered is False
    assert registration.issues == (
        "missing_account_binding",
        "missing_budget",
        "missing_spend_repository",
        "incompatible_fixture_release",
    )


def test_legacy_compatibility_projection_is_explicit_not_an_authority(tmp_path):
    service, _ = _service(tmp_path)
    _ready(service, ProviderName.DUCKDUCKGO)
    snapshot = service.snapshot(
        ProviderName.DUCKDUCKGO, egress="local", request_class="discovery"
    )
    projection = snapshot.as_legacy_status()
    assert projection["effective_status"] == "healthy"
    assert projection["authority"] == "provider_readiness"


def test_alembic_0008_is_additive_and_refuses_evidence_destroying_downgrade(
    tmp_path,
):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    path = tmp_path / "migration.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "0008_provider_readiness")
    engine = create_engine(f"sqlite:///{path}")
    tables = set(inspect(engine).get_table_names())
    assert {
        "provider_readiness_observations",
        "provider_readiness_snapshots",
        "provider_readiness_evidence_refs",
        "provider_readiness_leases",
        "provider_readiness_alert_dedupe",
    } <= tables
    assert "provider_spend_attempts" in tables
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO provider_readiness_observations "
                "(id, provider, dimension, state, source, scope_key, scope_json, "
                "producer_observed_at, ingested_at, expires_at, evidence_ref, "
                "safe_reason, protected) VALUES "
                "('evidence', 'brave', 'spend', 'exhausted', 'test', 'scope', "
                "'{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, "
                "'receipt', 'terminal', 1)"
            )
        )
    with pytest.raises(RuntimeError, match="evidence"):
        command.downgrade(config, "0007_extraction_outcomes")
    assert "provider_readiness_observations" in set(
        inspect(engine).get_table_names()
    )
