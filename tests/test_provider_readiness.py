from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

import pytest

from argus.models import ProviderName, is_adapter_provider


UTC = timezone.utc
ATTESTED_RELEASE = "argus-1.6.2"
ATTESTED_CONTRACT = "2026-07-27-v1"


def _service(tmp_path):
    from argus.broker.readiness import ProviderReadinessService
    from argus.persistence.readiness import create_readiness_repository

    repository = create_readiness_repository(f"sqlite:///{tmp_path / 'readiness.db'}")
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
            machine="test-machine",
            request_class=request_class,
            release_revision=ATTESTED_RELEASE,
            contract_version=ATTESTED_CONTRACT,
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
        release_revision=ATTESTED_RELEASE,
        contract_version=ATTESTED_CONTRACT,
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
        release_revision=ATTESTED_RELEASE,
        contract_version=ATTESTED_CONTRACT,
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
            release_revision=ATTESTED_RELEASE,
            contract_version=ATTESTED_CONTRACT,
        )
    )

    assert decision.code == "policy_skipped"
    assert decision.reason == "free_only"


def test_authority_clock_rejects_future_skew_and_expires_to_unknown(tmp_path):
    service, repository = _service(tmp_path)
    future = _observation(
        ProviderName.BRAVE, "reachability", "reachable", receipt="future"
    )
    object.__setattr__(future, "observed_at", datetime.now(UTC) + timedelta(seconds=31))
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


@pytest.mark.parametrize(
    ("dimension", "state", "source", "protected"),
    [
        ("reachability", "reachable", "provider_authoritative", True),
        ("spend", "available", "provider_authoritative", True),
        ("spend", "exhausted", "test_fixture", True),
        ("spend", "exhausted", "provider_authoritative", False),
    ],
)
def test_exact_expiry_model_is_terminal_spend_authority_only(
    dimension,
    state,
    source,
    protected,
):
    from argus.broker.readiness import ProviderObservation, ReadinessScope

    observed_at = datetime.now(UTC)
    with pytest.raises(ValueError, match="exact expiry"):
        ProviderObservation(
            provider=ProviderName.BRAVE,
            dimension=dimension,
            state=state,
            source=source,
            scope=ReadinessScope(account_fingerprint="account:v1"),
            observed_at=observed_at,
            ttl_seconds=None,
            protected=protected,
            expires_at=observed_at + timedelta(hours=1),
        )


def test_exact_expiry_model_and_persistence_reject_100_year_reachability(
    tmp_path,
):
    from argus.broker.readiness import ProviderObservation, ReadinessScope

    service, repository = _service(tmp_path)
    observed_at = repository.authority_now()
    reachable = _observation(
        ProviderName.BRAVE,
        "reachability",
        "reachable",
        receipt="exact-expiry-100-year",
        ttl_seconds=None,
        protected=True,
    )
    object.__setattr__(reachable, "source", "provider_authoritative")
    with pytest.raises(ValueError, match="exact expiry"):
        replace(
            reachable,
            expires_at=observed_at + timedelta(days=365 * 100),
        )

    object.__setattr__(
        reachable,
        "expires_at",
        observed_at + timedelta(days=365 * 100),
    )
    with pytest.raises(ValueError, match="exact expiry"):
        service.record_observation(reachable)

    with pytest.raises(ValueError, match="bounded to one year"):
        ProviderObservation(
            provider=ProviderName.BRAVE,
            dimension="spend",
            state="exhausted",
            source="provider_authoritative",
            scope=ReadinessScope(account_fingerprint="account:v1"),
            observed_at=observed_at,
            ttl_seconds=None,
            evidence_ref="exact-expiry-model-100-year",
            protected=True,
            expires_at=observed_at + timedelta(days=365 * 100),
        )

    with pytest.raises(ValueError, match="one-year bound"):
        repository.record_terminal_exhaustion(
            provider=ProviderName.BRAVE,
            account_fingerprint="account:v1",
            recurring=True,
            reset_at=observed_at + timedelta(days=365 * 100),
            evidence_ref="exact-expiry-persistence-100-year",
        )
    assert [
        row
        for row in repository.observations(ProviderName.BRAVE)
        if row.evidence_ref == "exact-expiry-persistence-100-year"
    ] == []


def test_generic_observation_path_rejects_semantic_exact_expiry(tmp_path):
    service, repository = _service(tmp_path)
    observed_at = repository.authority_now()
    terminal = _observation(
        ProviderName.BRAVE,
        "spend",
        "exhausted",
        receipt="generic-terminal-expiry",
        ttl_seconds=None,
        protected=True,
    )
    object.__setattr__(terminal, "source", "provider_authoritative")
    object.__setattr__(
        terminal,
        "expires_at",
        observed_at + timedelta(hours=1),
    )

    with pytest.raises(ValueError, match="exact expiry.*authorized"):
        service.record_observation(terminal)


def test_persistence_rejects_forged_exact_expiry_before_mutating_sqlite_state(
    tmp_path,
):
    from argus.broker.readiness import ProviderObservation, ReadinessScope

    service, repository = _service(tmp_path)
    service.record_observation(
        _observation(
            ProviderName.BRAVE,
            "spend",
            "available",
            receipt="existing-spend",
        )
    )
    scope = ReadinessScope(
        egress="local",
        machine="test-machine",
        request_class="discovery",
        release_revision=ATTESTED_RELEASE,
        contract_version=ATTESTED_CONTRACT,
        configuration_fingerprint="config:v1",
        credential_version_fingerprint="secret-version:v1",
        account_fingerprint="account:v1",
    )
    database_now = repository.authority_now()
    observation = ProviderObservation(
        provider=ProviderName.BRAVE,
        dimension="spend",
        state="exhausted",
        source="provider_authoritative",
        scope=scope,
        observed_at=database_now - timedelta(days=1),
        ttl_seconds=None,
        evidence_ref="forged-terminal",
        protected=True,
        expires_at=database_now + timedelta(days=1),
    )
    before_observations = repository.observations(ProviderName.BRAVE)
    before_snapshot = repository.read_snapshot(ProviderName.BRAVE, scope.fingerprint())

    object.__setattr__(
        observation, "observed_at", observation.observed_at.replace(tzinfo=None)
    )
    with pytest.raises(ValueError, match="observed_at.*timezone aware"):
        repository.record_and_materialize(
            observation,
            lambda *_args: {"forged": True},
            _allow_exact_expiry=True,
        )

    assert repository.observations(ProviderName.BRAVE) == before_observations
    assert (
        repository.read_snapshot(ProviderName.BRAVE, scope.fingerprint())
        == before_snapshot
    )


@pytest.mark.parametrize(
    ("producer_offset", "mutation", "message"),
    [
        (
            timedelta(seconds=20),
            lambda observation: object.__setattr__(
                observation,
                "expires_at",
                observation.expires_at.replace(tzinfo=None),
            ),
            "expires_at.*timezone aware",
        ),
        (
            timedelta(seconds=20),
            lambda observation: object.__setattr__(observation, "ttl_seconds", 1),
            "TTL or expires_at",
        ),
        (
            timedelta(seconds=20),
            lambda observation: object.__setattr__(
                observation, "expires_at", observation.observed_at
            ),
            "after observed_at",
        ),
        (
            timedelta(seconds=20),
            lambda observation: object.__setattr__(
                observation,
                "expires_at",
                observation.observed_at - timedelta(microseconds=1),
            ),
            "after observed_at",
        ),
        (
            -timedelta(days=1),
            lambda observation: object.__setattr__(
                observation,
                "expires_at",
                observation.observed_at + timedelta(days=365, microseconds=1),
            ),
            "one-year",
        ),
    ],
)
def test_persistence_rejects_forged_exact_expiry_invariants(
    tmp_path,
    producer_offset,
    mutation,
    message,
):
    from argus.broker.readiness import ProviderObservation, ReadinessScope

    _, repository = _service(tmp_path)
    database_now = repository.authority_now()
    producer = database_now + producer_offset
    observation = ProviderObservation(
        provider=ProviderName.BRAVE,
        dimension="spend",
        state="exhausted",
        source="provider_authoritative",
        scope=ReadinessScope(account_fingerprint="account:v1"),
        observed_at=producer,
        ttl_seconds=None,
        evidence_ref="forged-terminal",
        protected=True,
        expires_at=producer + timedelta(days=364),
    )
    mutation(observation)

    with pytest.raises(ValueError, match=message):
        repository.record_and_materialize(
            observation,
            lambda *_args: {"forged": True},
            _allow_exact_expiry=True,
        )


def test_persistence_allows_exact_expiry_at_producer_one_year_boundary(tmp_path):
    from argus.broker.readiness import ProviderObservation, ReadinessScope

    _, repository = _service(tmp_path)
    producer = repository.authority_now() - timedelta(days=1)
    observation = ProviderObservation(
        provider=ProviderName.BRAVE,
        dimension="spend",
        state="exhausted",
        source="provider_authoritative",
        scope=ReadinessScope(account_fingerprint="account:v1"),
        observed_at=producer,
        ttl_seconds=None,
        evidence_ref="exact-one-year",
        protected=True,
        expires_at=producer + timedelta(days=365),
    )

    repository.record_and_materialize(
        observation,
        lambda *_args: {"exact_boundary": True},
        _allow_exact_expiry=True,
    )

    assert [
        row
        for row in repository.observations(ProviderName.BRAVE)
        if row.evidence_ref == "exact-one-year"
    ]


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
    assert len(snapshot.evidence_receipts) == 1
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
    assert overflow.protected_evidence_count == 1
    assert len(overflow.evidence_receipts) == 2
    assert overflow.evidence_receipts[0].startswith("overflow:")
    assert "terminal-32" in overflow.evidence_receipts


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
    service.register_provider(_registration())
    assert service.authorize_probe(ProviderName.BRAVE, "fixture").allowed
    assert service.authorize_probe(ProviderName.BRAVE, "local_component").allowed
    assert not service.authorize_probe(ProviderName.BRAVE, "billable_search").allowed
    assert not service.authorize_probe(ProviderName.BRAVE, "unknown").allowed
    assert not service.authorize_probe(ProviderName.BRAVE, "no_spend_account").allowed
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


def test_live_probe_authorization_is_durable_bound_and_exactly_once(tmp_path):
    from argus.broker.readiness import ProbeAuthorization
    from argus.persistence.readiness import ReadinessConflict

    service, repository = _service(tmp_path)
    service.register_provider(_registration())
    authorization = ProbeAuthorization(
        workflow="explicit_validation",
        provider=ProviderName.BRAVE,
        idempotency_key="probe:brave:1",
        durable_receipt="receipt:brave:1",
        conservative_charge=0.5,
    )

    decision = service.authorize_probe(
        ProviderName.BRAVE, "billable_search", authorization
    )
    assert decision.allowed
    assert decision.authorization_id
    repository.consume_probe_once(
        provider=ProviderName.BRAVE,
        idempotency_key="probe:brave:1",
        durable_receipt="receipt:brave:1",
        attempt_id=decision.attempt_id,
    )
    with pytest.raises(ReadinessConflict, match="already consumed"):
        repository.consume_probe_once(
            provider=ProviderName.BRAVE,
            idempotency_key="probe:brave:1",
            durable_receipt="receipt:brave:1",
            attempt_id=decision.attempt_id,
        )


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
    assert (
        restarted.spend_state(ProviderName.SERPER, account_fingerprint="account:v1")
        == "exhausted"
    )
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
    assert (
        repository.spend_state(ProviderName.BRAVE, account_fingerprint="account:v1")
        == "exhausted"
    )
    repository.advance_authority_clock_for_test(timedelta(minutes=2))
    assert (
        repository.spend_state(ProviderName.BRAVE, account_fingerprint="account:v1")
        == "unknown"
    )


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
    assert "provider_readiness_observations" in set(inspect(engine).get_table_names())


def _exact_scope(
    *,
    provider: ProviderName = ProviderName.BRAVE,
    account: str = "account:v1",
    config: str = "config:v1",
    credential: str = "secret-version:v1",
    egress: str = "local",
    machine: str = "test-machine",
    request_class: str = "discovery",
    release: str = ATTESTED_RELEASE,
    contract: str = ATTESTED_CONTRACT,
):
    from argus.broker.readiness import ReadinessScope

    del provider
    return ReadinessScope(
        egress=egress,
        machine=machine,
        request_class=request_class,
        release_revision=release,
        contract_version=contract,
        configuration_fingerprint=config,
        credential_version_fingerprint=credential,
        account_fingerprint=account,
    )


def _registration(
    provider: ProviderName = ProviderName.BRAVE,
    *,
    enabled: bool = True,
    credential: str | None = "secret-version:v1",
    account: str | None = "account:v1",
    budget: float | None = 100.0,
    durable_spend: bool = True,
    release: str | None = ATTESTED_RELEASE,
    contract: str | None = ATTESTED_CONTRACT,
    fixture: str | None = "runtime",
):
    from argus.broker.readiness import ProviderRegistrationSpec
    from argus.providers.fixture_attestation import build_fixture_attestation

    if fixture == "runtime" and release and contract:
        fixture, fixture_attestation = build_fixture_attestation(
            provider,
            release=release,
            provider_contract=contract,
        )
    else:
        fixture_attestation = (
            {
                "provider": provider.value,
                "release": release,
                "adapter_module": f"argus.providers.{provider.value}",
                "adapter_code_sha256": "adapter-sha",
                "shared_adapter_sha256": "shared-sha",
                "fixture_manifest_sha256": "manifest-sha",
                "fixture_case_digest": "cases-sha",
                "request_contract": "search-request-v1",
                "response_contract": "provider-batch-v1",
                "provider_contract": contract,
            }
            if fixture and release and contract
            else None
        )

    return ProviderRegistrationSpec(
        provider=provider,
        enabled=enabled,
        configuration_fingerprint="config:v1",
        credential_version_fingerprint=credential,
        account_fingerprint=account,
        budget_limit=budget,
        durable_spend_repository=durable_spend,
        release_revision=release,
        contract_version=contract,
        fixture_evidence_ref=fixture,
        fixture_attestation=fixture_attestation,
    )


def test_default_snapshot_uses_registered_active_egress(tmp_path):
    service, _ = _service(tmp_path)
    service.register_provider(
        replace(_registration(provider=ProviderName.DUCKDUCKGO), egress="residential")
    )
    for dimension, state in (
        ("reachability", "reachable"),
        ("usability", "usable"),
    ):
        service.record_observation(
            _observation(
                ProviderName.DUCKDUCKGO,
                dimension,
                state,
                receipt=f"residential-{dimension}",
                egress="residential",
            )
        )

    snapshot = service.render_snapshot(ProviderName.DUCKDUCKGO)

    assert snapshot.reachability == "reachable"
    assert snapshot.usability == "usable"
    assert snapshot.healthy is True


def test_registration_persists_all_issues_and_never_calls_adapter_availability(
    tmp_path,
):
    from argus.broker.readiness import ProviderReadinessService
    from argus.persistence.readiness import create_readiness_repository

    class ExplodingAdapter:
        def is_available(self):
            raise AssertionError("adapter availability is not readiness")

    url = f"sqlite:///{tmp_path / 'registry.db'}"
    repository = create_readiness_repository(url)
    service = ProviderReadinessService(repository=repository)
    decision = service.register_provider(
        _registration(
            enabled=False,
            credential=None,
            account=None,
            budget=float("inf"),
            durable_spend=False,
            release=None,
            contract=None,
            fixture=None,
        ),
        adapter=ExplodingAdapter(),
    )

    assert decision.registered is False
    assert decision.issues == (
        "disabled_by_config",
        "missing_credential",
        "missing_account_binding",
        "missing_budget",
        "missing_spend_repository",
        "incompatible_fixture_release",
    )
    restarted = ProviderReadinessService(repository=create_readiness_repository(url))
    assert restarted.registration(ProviderName.BRAVE).issues == decision.issues


def test_runtime_registry_requires_paid_key_in_addition_to_credential_fingerprint(
    tmp_path,
    monkeypatch,
):
    from argus.broker.readiness import (
        ExecutableProviderRegistry,
        ProviderReadinessService,
    )
    from argus.config import ArgusConfig, ProviderConfig
    from argus.persistence.readiness import create_readiness_repository
    from argus.providers.brave import BraveProvider
    from argus.providers.duckduckgo import DuckDuckGoProvider

    monkeypatch.setenv(
        "ARGUS_BRAVE_CREDENTIAL_VERSION_FINGERPRINT", "brave-key-version:v1"
    )
    monkeypatch.setenv("ARGUS_BRAVE_ACCOUNT_FINGERPRINT", "brave-account:v1")
    monkeypatch.setenv("ARGUS_RELEASE_REVISION", "argus-1.6.2")
    brave_config = ProviderConfig(
        enabled=True,
        api_key="",
        monthly_budget_usd=10.0,
    )
    duckduckgo_config = ProviderConfig(enabled=True)
    config = ArgusConfig(
        brave=brave_config,
        duckduckgo=duckduckgo_config,
    )
    adapters = {
        ProviderName.BRAVE: BraveProvider(brave_config),
        ProviderName.DUCKDUCKGO: DuckDuckGoProvider(duckduckgo_config),
    }

    registry = ExecutableProviderRegistry.from_runtime(
        config=config,
        providers=adapters,
        durable_spend_repository=True,
    )
    specs = {spec.provider: spec for spec in registry.specs}
    assert specs[ProviderName.BRAVE].credential_version_fingerprint is None
    assert (
        specs[ProviderName.DUCKDUCKGO].credential_version_fingerprint
        == "not-applicable-credential"
    )
    configured_brave = ProviderConfig(
        enabled=True,
        api_key="fixture-key",
        monthly_budget_usd=10.0,
    )
    configured_registry = ExecutableProviderRegistry.from_runtime(
        config=ArgusConfig(brave=configured_brave),
        providers={ProviderName.BRAVE: BraveProvider(configured_brave)},
        durable_spend_repository=True,
    )
    assert (
        configured_registry.specs[0].credential_version_fingerprint
        == "brave-key-version:v1"
    )

    service = ProviderReadinessService(
        repository=create_readiness_repository(
            f"sqlite:///{tmp_path / 'runtime-registry.db'}"
        )
    )
    registry.persist(service, adapters)

    brave = service.registration(ProviderName.BRAVE)
    assert brave.registered is False
    assert "missing_credential" in brave.issues
    assert service.registration(ProviderName.DUCKDUCKGO).registered is True


def test_reregistration_preserves_protected_terminal_spend(tmp_path):
    service, _ = _service(tmp_path)
    spec = _registration(provider=ProviderName.SERPER)
    service.register_provider(spec)
    _ready(service, ProviderName.SERPER)
    service.record_observation(
        _observation(
            ProviderName.SERPER,
            "spend",
            "exhausted",
            receipt="provider:serper:exhausted",
            ttl_seconds=None,
            protected=True,
        )
    )

    service.register_provider(spec)

    snapshot = service.snapshot_for_scope(
        ProviderName.SERPER,
        _exact_scope(provider=ProviderName.SERPER),
    )
    assert snapshot.spend == "exhausted"
    assert snapshot.execution_decision.code == "spend_blocked"


def test_startup_materializes_all_search_mode_scopes(tmp_path):
    service, repository = _service(tmp_path)
    service.register_provider(_registration())

    for request_class in ("discovery", "research", "recovery", "grounding"):
        scope = _exact_scope(request_class=request_class)
        assert (
            repository.get_snapshot(ProviderName.BRAVE, scope.fingerprint()) is not None
        )
        rendered = service.snapshot_for_scope(ProviderName.BRAVE, scope)
        assert rendered.compatibility == "compatible"
        assert rendered.reachability == "unknown"
        assert rendered.usability == "unknown"
        assert rendered.healthy is False
        assert rendered.execution_decision.code == "eligible"


def test_different_modes_share_one_provider_account_budget_lock(tmp_path):
    from argus.broker.readiness import ExecutionContext
    from argus.persistence.readiness import create_readiness_repository

    url = f"sqlite:///{tmp_path / 'cross-mode-budget.db'}"
    first = __import__(
        "argus.broker.readiness", fromlist=["ProviderReadinessService"]
    ).ProviderReadinessService(repository=create_readiness_repository(url))
    first.register_provider(_registration(budget=1.5))
    second = __import__(
        "argus.broker.readiness", fromlist=["ProviderReadinessService"]
    ).ProviderReadinessService(repository=create_readiness_repository(url))
    barrier = Barrier(2)
    results = []

    def authorize(service, mode, owner):
        barrier.wait()
        results.append(
            service.authorize_execution(
                ExecutionContext(
                    provider=ProviderName.BRAVE,
                    tier=1,
                    plan_providers=(ProviderName.BRAVE,),
                    free_only=False,
                    caller_tier_cap=1,
                    scope=_exact_scope(request_class=mode),
                    plan_id=f"plan:{mode}",
                    caller_identity="test",
                    idempotency_key=f"request:{mode}",
                ),
                owner=owner,
                conservative_charge=1.0,
                execution_timeout_seconds=30,
            )
        )

    threads = [
        Thread(target=authorize, args=(first, "discovery", "worker-1")),
        Thread(target=authorize, args=(second, "research", "worker-2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len([result for result in results if result.allowed]) == 1
    assert first.repository.paid_attempt_count(ProviderName.BRAVE) == 1


def test_terminal_spend_survives_credential_rotation_for_account_all_modes(
    tmp_path,
):
    service, _ = _service(tmp_path)
    service.register_provider(_registration(provider=ProviderName.SERPER))
    service.repository.record_terminal_exhaustion(
        provider=ProviderName.SERPER,
        account_fingerprint="account:v1",
        recurring=False,
        reset_at=None,
        evidence_ref="provider:terminal:v1",
    )

    service.register_provider(
        _registration(
            provider=ProviderName.SERPER,
            credential="secret-version:v2",
        )
    )

    for mode in ("discovery", "research", "recovery", "grounding"):
        scope = _exact_scope(
            provider=ProviderName.SERPER,
            request_class=mode,
            credential="secret-version:v2",
        )
        assert service.snapshot_for_scope(ProviderName.SERPER, scope).spend == (
            "exhausted"
        )

    service.register_provider(
        _registration(
            provider=ProviderName.SERPER,
            account="account:v2",
            credential="secret-version:v3",
        )
    )
    assert (
        service.snapshot(ProviderName.SERPER, request_class="discovery").spend
        == "available"
    )


def test_completion_materializes_evidence_on_authorized_mode_only(tmp_path):
    from argus.broker.readiness import ExecutionContext

    service, _ = _service(tmp_path)
    service.register_provider(_registration())
    authorization = service.authorize_execution(
        ExecutionContext(
            provider=ProviderName.BRAVE,
            tier=1,
            plan_providers=(ProviderName.BRAVE,),
            free_only=False,
            caller_tier_cap=1,
            scope=_exact_scope(request_class="research"),
            plan_id="plan:research",
            caller_identity="test",
            idempotency_key="request:research",
        ),
        owner="research-worker",
        conservative_charge=1.0,
        execution_timeout_seconds=30,
    )
    assert authorization.allowed
    service.complete_execution(
        authorization,
        failure=None,
        actual_charge=0.0,
        charge_known=True,
        evidence_ref="execution:research",
    )

    research = service.snapshot_for_scope(
        ProviderName.BRAVE, _exact_scope(request_class="research")
    )
    discovery = service.snapshot_for_scope(
        ProviderName.BRAVE, _exact_scope(request_class="discovery")
    )
    assert "execution:research" in research.evidence_receipts
    assert "execution:research" not in discovery.evidence_receipts


def test_fixture_attestation_change_replaces_compatibility_evidence(tmp_path):
    service, _ = _service(tmp_path)
    service.register_provider(_registration())
    assert service.run_fixture_probe(ProviderName.BRAVE).allowed

    changed = _registration(fixture="attestation:manifest:v2")
    object.__setattr__(
        changed,
        "fixture_attestation",
        {
            **dict(changed.fixture_attestation or {}),
            "fixture_case_digest": "cases-v2",
            "provider_contract": "wrong-contract",
        },
    )
    service.register_provider(changed)

    snapshot = service.snapshot(
        ProviderName.BRAVE,
    )
    assert snapshot.compatibility == "unknown"
    assert snapshot.registration == "not_registered"


def test_cancellation_is_termination_indeterminate_even_for_free_provider(tmp_path):
    from argus.broker.readiness import ExecutionContext

    service, repository = _service(tmp_path)
    service.register_provider(
        _registration(
            provider=ProviderName.YAHOO,
            account="not-applicable-account",
            budget=None,
        )
    )
    context = ExecutionContext(
        provider=ProviderName.YAHOO,
        tier=0,
        plan_providers=(ProviderName.YAHOO,),
        free_only=False,
        caller_tier_cap=0,
        scope=_exact_scope(
            provider=ProviderName.YAHOO,
            account="not-applicable-account",
        ),
        plan_id="plan:yahoo",
        caller_identity="test",
        idempotency_key="request:yahoo:1",
    )
    authorization = service.authorize_execution(
        context,
        owner="worker",
        conservative_charge=0.0,
        execution_timeout_seconds=30,
    )
    assert authorization.allowed
    service.complete_execution(
        authorization,
        failure=None,
        actual_charge=0.0,
        charge_known=True,
        termination_known=False,
        evidence_ref="cancelled:yahoo",
    )
    assert repository.latest_lease(ProviderName.YAHOO).outcome == (
        "termination_indeterminate"
    )
    replacement = service.authorize_execution(
        ExecutionContext(
            **{
                **context.as_dict(),
                "idempotency_key": "request:yahoo:2",
            }
        ),
        owner="worker-2",
        conservative_charge=0.0,
        execution_timeout_seconds=30,
    )
    assert replacement.allowed is False


def test_expired_unresolved_execution_still_blocks_replacement(tmp_path):
    from argus.broker.readiness import ExecutionContext

    service, repository = _service(tmp_path)
    service.register_provider(
        _registration(
            provider=ProviderName.YAHOO,
            account="not-applicable-account",
            budget=None,
        )
    )
    _ready(service, ProviderName.YAHOO)
    context = ExecutionContext(
        provider=ProviderName.YAHOO,
        tier=0,
        plan_providers=(ProviderName.YAHOO,),
        free_only=False,
        caller_tier_cap=0,
        scope=_exact_scope(
            provider=ProviderName.YAHOO,
            account="not-applicable-account",
        ),
        plan_id="plan:unresolved",
        caller_identity="test",
        idempotency_key="request:unresolved:1",
    )
    authorization = service.authorize_execution(
        context,
        owner="worker-1",
        conservative_charge=0.0,
        execution_timeout_seconds=1,
    )
    assert authorization.allowed
    service.complete_execution(
        authorization,
        failure=None,
        actual_charge=0.0,
        charge_known=True,
        termination_known=False,
        evidence_ref="execution:indeterminate",
    )

    repository.advance_authority_clock_for_test(timedelta(seconds=2))
    denied = service.authorize_execution(
        ExecutionContext(
            **{
                **context.as_dict(),
                "idempotency_key": "request:unresolved:2",
            }
        ),
        owner="worker-2",
        conservative_charge=0.0,
        execution_timeout_seconds=1,
    )

    assert denied.allowed is False
    assert denied.decision.reason == "attempt_in_flight"


def test_uncertain_paid_execution_blocks_other_account_modes_until_reconciled(
    tmp_path,
):
    from argus.broker.readiness import ExecutionContext

    service, _ = _service(tmp_path)
    service.register_provider(_registration())
    discovery = ExecutionContext(
        provider=ProviderName.BRAVE,
        tier=1,
        plan_providers=(ProviderName.BRAVE,),
        free_only=False,
        caller_tier_cap=1,
        scope=_exact_scope(request_class="discovery"),
        plan_id="plan:uncertain:discovery",
        caller_identity="test",
        idempotency_key="request:uncertain:discovery",
    )
    authorization = service.authorize_execution(
        discovery,
        owner="discovery-worker",
        conservative_charge=1.0,
        execution_timeout_seconds=30,
    )
    assert authorization.allowed
    service.complete_execution(
        authorization,
        failure=None,
        actual_charge=None,
        charge_known=False,
        evidence_ref="execution:uncertain:discovery",
    )

    for mode in ("discovery", "research", "recovery", "grounding"):
        assert (
            service.snapshot(ProviderName.BRAVE, request_class=mode).spend
            == "uncertain"
        )
    denied = service.authorize_execution(
        ExecutionContext(
            **{
                **discovery.as_dict(),
                "scope": _exact_scope(request_class="research"),
                "plan_id": "plan:uncertain:research",
                "idempotency_key": "request:uncertain:research",
            }
        ),
        owner="research-worker",
        conservative_charge=0.01,
        execution_timeout_seconds=30,
    )
    assert denied.allowed is False
    assert denied.decision.reason == "uncertain"


def test_matching_successful_settlement_clears_uncertain_account_all_modes(
    tmp_path,
):
    from argus.broker.readiness import ExecutionContext

    service, _ = _service(tmp_path)
    service.register_provider(_registration())
    authorization = service.authorize_execution(
        ExecutionContext(
            provider=ProviderName.BRAVE,
            tier=1,
            plan_providers=(ProviderName.BRAVE,),
            free_only=False,
            caller_tier_cap=1,
            scope=_exact_scope(request_class="discovery"),
            plan_id="plan:matched-resolution",
            caller_identity="test",
            idempotency_key="request:matched-resolution",
        ),
        owner="worker",
        conservative_charge=1.0,
        execution_timeout_seconds=30,
    )
    assert authorization.allowed
    service.complete_execution(
        authorization,
        failure=None,
        actual_charge=None,
        charge_known=False,
        evidence_ref="execution:matched-uncertain",
    )
    assert (
        service.snapshot(ProviderName.BRAVE, request_class="research").spend
        == "uncertain"
    )

    service.complete_execution(
        authorization,
        failure=None,
        actual_charge=0.25,
        charge_known=True,
        evidence_ref="execution:matched-resolved",
    )

    for mode in ("discovery", "research", "recovery", "grounding"):
        snapshot = service.snapshot(ProviderName.BRAVE, request_class=mode)
        assert snapshot.spend == "available"
        assert snapshot.execution_decision.code == "eligible"


def test_settlement_uses_same_provider_budget_lock_as_authorization(
    tmp_path,
    monkeypatch,
):
    from argus.broker.readiness import ExecutionContext

    service, repository = _service(tmp_path)
    service.register_provider(_registration())
    calls = []
    original = repository._lock_provider_budget

    def observe_lock(session, provider):
        calls.append(provider)
        return original(session, provider)

    monkeypatch.setattr(repository, "_lock_provider_budget", observe_lock)
    authorization = service.authorize_execution(
        ExecutionContext(
            provider=ProviderName.BRAVE,
            tier=1,
            plan_providers=(ProviderName.BRAVE,),
            free_only=False,
            caller_tier_cap=1,
            scope=_exact_scope(),
            plan_id="plan:lock",
            caller_identity="test",
            idempotency_key="request:lock",
        ),
        owner="worker",
        conservative_charge=1.0,
        execution_timeout_seconds=30,
    )
    assert authorization.allowed
    assert calls == [ProviderName.BRAVE]
    calls.clear()

    service.complete_execution(
        authorization,
        failure=None,
        actual_charge=None,
        charge_known=False,
        evidence_ref="execution:lock",
    )
    assert calls == [ProviderName.BRAVE]


def test_active_receipts_are_protected_before_overflow_compaction(tmp_path):
    from sqlalchemy import select
    from argus.persistence.readiness import ProviderReadinessEvidenceRefRow
    from argus.persistence.provider_spend import SpendAuditRow

    service, repository = _service(tmp_path)
    service.register_provider(_registration())
    dimensions = (
        ("registration", "registered"),
        ("configuration", "configured"),
        ("compatibility", "compatible"),
        ("reachability", "reachable"),
        ("usability", "usable"),
        ("cooldown", "clear"),
        ("spend", "available"),
    )
    for mode_index in range(5):
        for dimension, state in dimensions:
            service.record_observation(
                _observation(
                    ProviderName.BRAVE,
                    dimension,
                    state,
                    receipt=f"active:{mode_index}:{dimension}",
                    request_class=f"mode-{mode_index}",
                    ttl_seconds=None,
                )
            )

    with repository.session_factory() as session:
        active = session.scalar(
            select(ProviderReadinessEvidenceRefRow).where(
                ProviderReadinessEvidenceRefRow.provider == "brave",
                ProviderReadinessEvidenceRefRow.evidence_ref == "active:0:registration",
            )
        )
        assert active is not None
        assert active.protected is True
    assert any(
        row.dimension == "configuration" and row.state == "evidence_overflow"
        for row in repository.observations(ProviderName.BRAVE)
    )
    stale_scope = _exact_scope(request_class="mode-0")
    failed_closed = service.snapshot_for_scope(ProviderName.BRAVE, stale_scope)
    assert failed_closed.configuration.issues == ("evidence_overflow",)
    assert failed_closed.execution_decision.code == "unavailable"
    assert failed_closed.protected_evidence_count == 7
    assert any(
        receipt.startswith("overflow:") for receipt in failed_closed.evidence_receipts
    )
    with repository.session_factory() as session:
        evidence_count = len(
            list(
                session.scalars(
                    select(ProviderReadinessEvidenceRefRow).where(
                        ProviderReadinessEvidenceRefRow.provider == "brave",
                    )
                )
            )
        )
        overflow_audit = session.scalar(
            select(SpendAuditRow).where(
                SpendAuditRow.provider == "brave",
                SpendAuditRow.action == "readiness_overflow",
            )
        )
        archived_evidence = list(
            session.scalars(
                select(SpendAuditRow).where(
                    SpendAuditRow.provider == "brave",
                    SpendAuditRow.action == "readiness_evidence_archive",
                )
            )
        )
    assert evidence_count <= 32
    assert overflow_audit is not None
    overflow_payload = __import__("json").loads(overflow_audit.after_json)
    assert overflow_payload["protected_count"] == 36
    assert overflow_payload["omitted_count"] == 5
    assert overflow_payload["query"] == {
        "action": "readiness_evidence_archive",
        "provider": "brave",
        "source": "provider_spend_audit",
    }
    assert "omitted_refs" not in overflow_payload
    assert len(archived_evidence) == overflow_payload["protected_count"]
    assert all(
        set(__import__("json").loads(row.after_json)) == {"evidence_ref"}
        for row in archived_evidence
    )


def test_next_reset_does_not_exclude_current_period_spend(tmp_path):
    from dataclasses import replace
    from argus.broker.readiness import ExecutionContext

    service, _ = _service(tmp_path)
    now = service.repository.authority_now()
    service.register_provider(
        replace(
            _registration(budget=2.0),
            budget_period_started_at=now - timedelta(days=1),
            budget_next_reset_at=now + timedelta(days=29),
        )
    )
    first = service.authorize_execution(
        ExecutionContext(
            provider=ProviderName.BRAVE,
            tier=1,
            plan_providers=(ProviderName.BRAVE,),
            free_only=False,
            caller_tier_cap=1,
            scope=_exact_scope(),
            plan_id="period-plan",
            caller_identity="test",
            idempotency_key="period-request-1",
        ),
        owner="worker-1",
        conservative_charge=1.5,
        execution_timeout_seconds=30,
    )
    assert first.allowed
    service.complete_execution(
        first,
        failure=None,
        actual_charge=1.5,
        charge_known=True,
        evidence_ref="period-charge",
    )
    denied = service.authorize_execution(
        ExecutionContext(
            provider=ProviderName.BRAVE,
            tier=1,
            plan_providers=(ProviderName.BRAVE,),
            free_only=False,
            caller_tier_cap=1,
            scope=_exact_scope(request_class="research"),
            plan_id="period-plan-research",
            caller_identity="test",
            idempotency_key="period-request-2",
        ),
        owner="worker-2",
        conservative_charge=1.0,
        execution_timeout_seconds=30,
    )
    assert denied.allowed is False
    assert denied.decision.reason == "exhausted"


def test_paid_probe_owns_exact_attempt_and_final_result_receipt(tmp_path):
    from argus.broker.readiness import ProbeAuthorization
    from argus.persistence.provider_spend import (
        ProviderSpendRepository,
        SpendAuditRow,
    )
    from sqlalchemy import select

    service, repository = _service(tmp_path)
    service.register_provider(_registration(budget=5.0))
    decision = service.authorize_probe(
        ProviderName.BRAVE,
        "billable_search",
        ProbeAuthorization(
            workflow="explicit_validation",
            provider=ProviderName.BRAVE,
            idempotency_key="probe:paid:1",
            durable_receipt="probe:permission:1",
            conservative_charge=0.5,
        ),
    )
    assert decision.allowed
    assert decision.attempt_id
    attempt = ProviderSpendRepository(repository.session_factory).get_attempt(
        decision.attempt_id
    )
    assert attempt.status == "reserved"

    authorization = repository.consume_probe_once(
        provider=ProviderName.BRAVE,
        idempotency_key="probe:paid:1",
        durable_receipt="probe:permission:1",
        attempt_id=decision.attempt_id,
    )
    assert authorization.attempt_id == decision.attempt_id
    service.complete_execution(
        authorization,
        failure=None,
        actual_charge=0.0,
        charge_known=True,
        evidence_ref="probe:result:1",
        probe_idempotency_key="probe:paid:1",
    )

    with repository.session_factory() as session:
        result = session.scalar(
            select(SpendAuditRow).where(
                SpendAuditRow.action == "probe_result",
                SpendAuditRow.attempt_id == decision.attempt_id,
            )
        )
        assert result is not None
        assert "success" in result.after_json
    replay = service.authorize_probe(
        ProviderName.BRAVE,
        "billable_search",
        ProbeAuthorization(
            workflow="explicit_validation",
            provider=ProviderName.BRAVE,
            idempotency_key="probe:paid:1",
            durable_receipt="probe:permission:1",
            conservative_charge=0.5,
        ),
    )
    assert replay.allowed is False
    assert replay.reason == "probe_already_consumed"


@pytest.mark.parametrize(
    "provider",
    tuple(provider for provider in ProviderName if is_adapter_provider(provider)),
)
def test_fixture_attestation_executes_all_cases_and_recomputes_content(
    provider,
):
    from argus.providers.fixture_attestation import (
        build_fixture_attestation,
        run_fixture_cases,
        verify_fixture_attestation,
    )

    evidence_ref, attestation = build_fixture_attestation(
        provider,
        release=ATTESTED_RELEASE,
        provider_contract=ATTESTED_CONTRACT,
    )
    assert run_fixture_cases(provider) == attestation["fixture_case_digest"]
    assert verify_fixture_attestation(
        attestation,
        evidence_ref=evidence_ref,
    )
    assert not verify_fixture_attestation(
        {**attestation, "shared_adapter_sha256": "tampered"},
        evidence_ref=evidence_ref,
    )


def test_fixture_attestation_rejects_noncanonical_adapter_substitution():
    from argus.providers.fixture_attestation import (
        build_fixture_attestation,
        verify_fixture_attestation,
    )

    evidence_ref, attestation = build_fixture_attestation(
        ProviderName.BRAVE,
        release=ATTESTED_RELEASE,
        provider_contract=ATTESTED_CONTRACT,
    )
    assert attestation["adapter_module"] == "argus.providers.brave"
    assert attestation["adapter_class"] == "BraveProvider"
    assert not verify_fixture_attestation(
        {**attestation, "adapter_class": "BaseProvider"},
        evidence_ref=evidence_ref,
    )
    with pytest.raises(ValueError, match="canonical adapter"):
        build_fixture_attestation(
            ProviderName.BRAVE,
            release=ATTESTED_RELEASE,
            provider_contract=ATTESTED_CONTRACT,
            adapter_module="argus.providers.base",
        )


def test_fixture_attestation_requires_exact_release_and_provider_contract():
    from argus.providers.fixture_attestation import build_fixture_attestation

    _, checked = build_fixture_attestation(
        ProviderName.BRAVE,
        release=ATTESTED_RELEASE,
        provider_contract=ATTESTED_CONTRACT,
    )
    with pytest.raises(ValueError, match="release"):
        build_fixture_attestation(
            ProviderName.BRAVE,
            release="unattested-release",
            provider_contract=checked["provider_contract"],
        )
    with pytest.raises(ValueError, match="contract"):
        build_fixture_attestation(
            ProviderName.BRAVE,
            release=checked["release"],
            provider_contract="unattested-contract",
        )


def test_fixture_attestation_hashes_full_request_shape_seam():
    from argus.providers.fixture_attestation import (
        build_fixture_attestation,
        verify_fixture_attestation,
    )

    _, attestation = build_fixture_attestation(
        ProviderName.BRAVE,
        release=ATTESTED_RELEASE,
        provider_contract=ATTESTED_CONTRACT,
    )
    dependencies = set(attestation["shared_dependency_files"])
    assert {
        "models.py",
        "config.py",
        "provider_controls.py",
        "broker/planning.py",
        "broker/provider_evidence.py",
        "providers/base.py",
        "providers/ddg_worker.py",
        "providers/normalization.py",
        "providers/fixture_golden_contracts.py",
    } <= dependencies
    assert len(attestation["golden_contract_sha256"]) == 64
    assert not verify_fixture_attestation(
        {
            **attestation,
            "golden_contract_sha256": "tampered",
        }
    )


def test_fixture_harness_enforces_exact_outcomes_for_all_canonical_adapters():
    from argus.broker.provider_evidence import FailureCategory
    from argus.providers.fixture_harness import run_fixture_case_summaries

    for provider in ProviderName:
        if not is_adapter_provider(provider):
            continue
        summaries = run_fixture_case_summaries(provider)
        assert summaries["success"]["observations"] == 1
        assert summaries["success"]["failure"] is None
        assert summaries["empty"]["observations"] == 0
        assert summaries["empty"]["failure"] == FailureCategory.EMPTY.value
        assert summaries["error"]["failure"] == "rate_limited"
        assert summaries["malformed"]["failure"] == (FailureCategory.PARSE_ERROR.value)
        assert summaries["success"]["golden_request_validated"] is True
        assert summaries["success"]["golden_output_validated"] is True


def test_golden_provider_contract_is_checked_separately_from_attestations():
    from pathlib import Path

    from argus.providers.fixture_golden_contracts import (
        GOLDEN_PROVIDER_CONTRACTS,
    )

    assert set(GOLDEN_PROVIDER_CONTRACTS) == {
        provider for provider in ProviderName if is_adapter_provider(provider)
    }
    for contract in GOLDEN_PROVIDER_CONTRACTS.values():
        assert contract["provider_contract_version"] == ATTESTED_CONTRACT
        assert set(contract["expected"]) == {
            "success",
            "empty",
            "error",
            "malformed",
            "privacy",
        }
        assert contract["request"]["method"] in {"GET", "POST", "SUBPROCESS"}
    generator = Path("scripts/generate_provider_fixture_attestations.py").read_text()
    assert generator.count("write_text(") == 1
    assert "attestation_artifact_path().write_text" in generator


def test_attestation_regeneration_rejects_wrong_outbound_query(monkeypatch):
    from argus.providers.brave import BraveProvider
    from scripts.generate_provider_fixture_attestations import (
        generate_attestation_document,
    )

    original = BraveProvider.search

    async def wrong_query(self, query):
        query.query = "wrong-query"
        return await original(self, query)

    monkeypatch.setattr(BraveProvider, "search", wrong_query)
    with pytest.raises(ValueError, match="golden request"):
        generate_attestation_document()


def test_attestation_regeneration_rejects_wrong_provider_contract(monkeypatch):
    import argus.providers.normalization as normalization

    from scripts.generate_provider_fixture_attestations import (
        generate_attestation_document,
    )

    monkeypatch.setitem(
        normalization._CONTRACT_VERSION,
        ProviderName.BRAVE,
        "wrong-contract",
    )
    with pytest.raises(ValueError, match="golden output"):
        generate_attestation_document()


def test_attestation_regeneration_rejects_wrong_normalized_title(monkeypatch):
    import argus.providers.normalization as normalization

    from scripts.generate_provider_fixture_attestations import (
        generate_attestation_document,
    )

    original = normalization._fields

    def wrong_title(provider, item):
        url, title, snippet, kind = original(provider, item)
        if provider is ProviderName.BRAVE:
            title = "Wrong title"
        return url, title, snippet, kind

    monkeypatch.setattr(normalization, "_fields", wrong_title)
    with pytest.raises(ValueError, match="golden output"):
        generate_attestation_document()


def test_golden_transport_rejects_brave_cookie_and_url_query_leaks(monkeypatch):
    import httpx

    from argus.providers.brave import BRAVE_API_BASE, BraveProvider
    from scripts.generate_provider_fixture_attestations import (
        generate_attestation_document,
    )

    original = BraveProvider.search

    async def leaking_transport(self, query):
        async with httpx.AsyncClient(cookies={"fixture": query.query}) as client:
            await client.get(
                f"{BRAVE_API_BASE}?debug={query.query}",
                cookies={"fixture": query.query},
            )
        return await original(self, query)

    monkeypatch.setattr(BraveProvider, "search", leaking_transport)
    with pytest.raises(ValueError, match="privacy"):
        generate_attestation_document()


def test_golden_transport_rejects_form_auth_and_extensions(monkeypatch):
    import httpx

    from argus.providers.brave import BRAVE_API_BASE, BraveProvider
    from scripts.generate_provider_fixture_attestations import (
        generate_attestation_document,
    )

    original = BraveProvider.search

    async def extra_channels(self, query):
        async with httpx.AsyncClient(auth=("fixture", "fixture")) as client:
            await client.post(
                BRAVE_API_BASE,
                data={"mode": "unexpected"},
                auth=("fixture", "fixture"),
                extensions={"fixture": True},
            )
        return await original(self, query)

    monkeypatch.setattr(BraveProvider, "search", extra_channels)
    with pytest.raises(ValueError, match="golden request"):
        generate_attestation_document()


def test_golden_transport_rejects_ddg_env_query_leak(monkeypatch):
    import asyncio
    import json
    import sys

    from argus.providers.duckduckgo import DuckDuckGoProvider
    from scripts.generate_provider_fixture_attestations import (
        generate_attestation_document,
    )

    original = DuckDuckGoProvider.search

    async def leaking_subprocess(self, query):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "argus.providers.ddg_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"FIXTURE_QUERY": query.query},
            limit=1_048_576,
        )
        await process.communicate(
            json.dumps(
                {
                    "query": query.query,
                    "max_results": 1,
                    "timelimit": None,
                }
            ).encode()
        )
        return await original(self, query)

    monkeypatch.setattr(DuckDuckGoProvider, "search", leaking_subprocess)
    with pytest.raises(ValueError, match="privacy"):
        generate_attestation_document()


def test_golden_transport_rejects_undeclared_ddg_env(monkeypatch):
    import asyncio
    import json
    import sys

    from argus.providers.duckduckgo import DuckDuckGoProvider
    from scripts.generate_provider_fixture_attestations import (
        generate_attestation_document,
    )

    original = DuckDuckGoProvider.search

    async def undeclared_env(self, query):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "argus.providers.ddg_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"FIXTURE_MODE": "unexpected"},
            limit=1_048_576,
        )
        await process.communicate(
            json.dumps(
                {
                    "query": query.query,
                    "max_results": 1,
                    "timelimit": None,
                }
            ).encode()
        )
        return await original(self, query)

    monkeypatch.setattr(DuckDuckGoProvider, "search", undeclared_env)
    with pytest.raises(ValueError, match="golden request"):
        generate_attestation_document()


def test_fixture_harness_rejects_private_query_in_adapter_logs(monkeypatch):
    import logging

    from argus.providers.brave import BraveProvider
    from argus.providers.fixture_harness import run_fixture_cases

    original = BraveProvider.search

    async def leaking_search(self, query):
        logging.getLogger("argus.providers.brave").warning(
            "leaked query %s", query.query
        )
        return await original(self, query)

    monkeypatch.setattr(BraveProvider, "search", leaking_search)
    with pytest.raises(ValueError, match="privacy"):
        run_fixture_cases(ProviderName.BRAVE)


def test_fixture_attestation_loads_checked_artifact_without_running_harness(
    monkeypatch,
):
    import argus.providers.fixture_attestation as fixture_attestation

    monkeypatch.setattr(
        fixture_attestation,
        "run_fixture_cases",
        lambda _provider: (_ for _ in ()).throw(
            AssertionError("startup must not execute fixture harness")
        ),
    )
    evidence_ref, attestation = fixture_attestation.build_fixture_attestation(
        ProviderName.BRAVE,
        release=ATTESTED_RELEASE,
        provider_contract=ATTESTED_CONTRACT,
    )
    assert fixture_attestation.verify_fixture_attestation(
        attestation, evidence_ref=evidence_ref
    )


def test_monkeypatched_canonical_adapter_fails_checked_attestation(monkeypatch):
    from argus.config import ProviderConfig
    from argus.providers.brave import BraveProvider
    from argus.providers.fixture_attestation import (
        build_fixture_attestation,
        verify_fixture_attestation,
    )

    evidence_ref, attestation = build_fixture_attestation(
        ProviderName.BRAVE,
        release=ATTESTED_RELEASE,
        provider_contract=ATTESTED_CONTRACT,
    )

    async def changed_search(self, query):
        del self, query
        raise AssertionError("changed adapter")

    monkeypatch.setattr(BraveProvider, "search", changed_search)
    assert not verify_fixture_attestation(attestation, evidence_ref=evidence_ref)
    assert BraveProvider(ProviderConfig(enabled=True, api_key="fixture"))


def test_generated_fixture_attestation_artifact_is_current():
    from scripts.generate_provider_fixture_attestations import (
        generate_attestation_document,
        load_attestation_document,
    )

    assert generate_attestation_document() == load_attestation_document()


def test_postgres_verifier_rejects_any_worker_exception():
    from scripts.verify_provider_readiness_postgres import (
        authorize_workers,
    )

    class Service:
        def __init__(self, fail):
            self.fail = fail

        def authorize_execution(self, *_args, **_kwargs):
            if self.fail:
                raise RuntimeError("worker failed")
            return object()

    contexts = {"discovery": object(), "research": object()}
    with pytest.raises(RuntimeError, match="worker"):
        authorize_workers(
            (
                (Service(False), "worker-1", "discovery"),
                (Service(True), "worker-2", "research"),
            ),
            contexts,
        )


def test_postgres_verifier_checks_exact_expiry_rejections_without_writes(tmp_path):
    from argus.broker.readiness import ReadinessScope
    from scripts.verify_provider_readiness_postgres import (
        verify_exact_expiry_boundary_rejections,
    )

    _, repository = _service(tmp_path)
    scope = ReadinessScope(account_fingerprint="account:v1")

    assert verify_exact_expiry_boundary_rejections(repository, scope, "sqlite-test")


def test_authoritative_zero_reconciliation_clears_terminal_account_all_modes(
    tmp_path,
):
    from argus.persistence.provider_spend import ProviderSpendRepository

    service, repository = _service(tmp_path)
    service.register_provider(_registration(provider=ProviderName.SERPER))
    spend = ProviderSpendRepository(repository.session_factory)
    attempt = spend.reserve(
        provider=ProviderName.SERPER,
        conservative_charge=1.0,
        budget_limit=10.0,
        caller_identity="provider:serper",
        caller_label="reconciliation",
        idempotency_key="reconcile-terminal-attempt",
    )
    repository.record_terminal_exhaustion(
        provider=ProviderName.SERPER,
        account_fingerprint="account:v1",
        recurring=False,
        reset_at=None,
        evidence_ref="terminal:serper",
    )
    snapshot = spend.record_provider_snapshot(
        provider=ProviderName.SERPER,
        balance=10.0,
        observed_at=repository.authority_now(),
        actor_identity="provider:serper",
        idempotency_key="reconcile-terminal-snapshot",
        provider_reference="serper-terminal-authority",
        related_attempt_id=attempt.attempt_id,
        authoritative_charge=0.0,
    )
    spend.resolve(
        attempt.attempt_id,
        actual_charge=0.0,
        outcome="confirmed_not_consumed",
        source="provider",
        actor_identity="provider:serper",
        idempotency_key="reconcile-terminal-resolution",
        provider_snapshot_id=snapshot.snapshot_id,
    )

    for mode in ("discovery", "research", "recovery", "grounding"):
        assert (
            service.snapshot(ProviderName.SERPER, request_class=mode).spend
            == "available"
        )


@pytest.mark.parametrize(
    ("start_delta", "reset_delta"),
    [
        (timedelta(seconds=1), timedelta(days=30)),
        (timedelta(days=-1), timedelta(seconds=-1)),
        (timedelta(days=-400), timedelta(days=30)),
        (timedelta(days=-1), timedelta(days=400)),
    ],
)
def test_budget_period_boundaries_use_database_time_and_fail_implausible(
    tmp_path,
    start_delta,
    reset_delta,
):
    from dataclasses import replace

    service, _ = _service(tmp_path)
    now = service.repository.authority_now()
    with pytest.raises(ValueError, match="budget"):
        service.register_provider(
            replace(
                _registration(),
                budget_period_started_at=now + start_delta,
                budget_next_reset_at=now + reset_delta,
            )
        )


def test_protected_evidence_overflow_is_bounded_and_unavailable(tmp_path):
    from sqlalchemy import select
    from argus.persistence.provider_spend import SpendAuditRow

    service, repository = _service(tmp_path)
    service.register_provider(_registration())
    _ready(service)

    for index in range(33):
        service.record_observation(
            _observation(
                ProviderName.BRAVE,
                "spend",
                "exhausted",
                receipt=f"protected:{index}",
                ttl_seconds=None,
                protected=True,
            )
        )

    snapshot = service.snapshot_for_scope(ProviderName.BRAVE, _exact_scope())
    assert snapshot.configuration.issues == ("evidence_overflow",)
    assert snapshot.execution_decision.code == "unavailable"
    assert repository.evidence_ref_count(ProviderName.BRAVE) == 32
    with repository.session_factory() as session:
        overflow = session.scalar(
            select(SpendAuditRow).where(
                SpendAuditRow.provider == "brave",
                SpendAuditRow.action == "readiness_overflow",
            )
        )
        archive = list(
            session.scalars(
                select(SpendAuditRow).where(
                    SpendAuditRow.provider == "brave",
                    SpendAuditRow.action == "readiness_evidence_archive",
                )
            )
        )
    payload = __import__("json").loads(overflow.after_json)
    assert payload["protected_count"] == len(archive)
    assert payload["protected_count"] >= 40


def test_registration_requires_exact_fixture_release_and_contract(tmp_path):
    service, _ = _service(tmp_path)
    service.register_provider(_registration())
    wrong = _observation(
        ProviderName.BRAVE,
        "compatibility",
        "compatible",
        receipt="fixture-wrong",
    )
    object.__setattr__(
        wrong,
        "scope",
        _exact_scope(release="release-other", contract="contract-other"),
    )
    service.record_observation(wrong)

    snapshot = service.snapshot_for_scope(ProviderName.BRAVE, _exact_scope())

    assert snapshot.compatibility == "compatible"
    assert "fixture-wrong" not in snapshot.evidence_receipts


@pytest.mark.parametrize(
    "field",
    (
        "egress",
        "machine",
        "request_class",
        "release_revision",
        "contract_version",
        "configuration_fingerprint",
        "credential_version_fingerprint",
        "account_fingerprint",
    ),
)
def test_scope_rejects_every_missing_identity(field):
    values = _exact_scope().as_dict()
    values[field] = None
    from argus.broker.readiness import ReadinessScope

    with pytest.raises(ValueError, match=field):
        ReadinessScope(**values)


def test_snapshot_read_uses_materialized_row_not_observation_history(tmp_path):
    service, repository = _service(tmp_path)
    service.register_provider(_registration())
    _ready(service)
    expected = service.snapshot_for_scope(ProviderName.BRAVE, _exact_scope())

    repository.observations = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("request path scanned observation history")
    )
    loaded = service.snapshot_for_scope(ProviderName.BRAVE, _exact_scope())

    assert loaded == expected


def test_repeated_success_resolves_old_evidence_and_never_overflows(tmp_path):
    service, repository = _service(tmp_path)
    service.register_provider(_registration())
    _ready(service)
    for index in range(33):
        service.record_observation(
            _observation(
                ProviderName.BRAVE,
                "usability",
                "usable",
                receipt=f"success-{index:02}",
            )
        )

    snapshot = service.snapshot_for_scope(ProviderName.BRAVE, _exact_scope())

    assert snapshot.execution_decision.code == "eligible"
    assert snapshot.protected_evidence_count == 0
    assert len(snapshot.evidence_receipts) <= 32
    assert repository.evidence_ref_count(ProviderName.BRAVE) <= 32


def test_atomic_authorization_rechecks_policy_and_grants_one_fenced_attempt(
    tmp_path,
):
    from argus.broker.readiness import ExecutionContext
    from argus.persistence.readiness import create_readiness_repository

    url = f"sqlite:///{tmp_path / 'authorize.db'}"
    first_service = __import__(
        "argus.broker.readiness", fromlist=["ProviderReadinessService"]
    ).ProviderReadinessService(repository=create_readiness_repository(url))
    first_service.register_provider(_registration())
    _ready(first_service)
    second_service = __import__(
        "argus.broker.readiness", fromlist=["ProviderReadinessService"]
    ).ProviderReadinessService(repository=create_readiness_repository(url))
    context = ExecutionContext(
        provider=ProviderName.BRAVE,
        tier=1,
        plan_providers=(ProviderName.BRAVE,),
        free_only=False,
        caller_tier_cap=1,
        scope=_exact_scope(),
        plan_id="plan:v1",
        caller_identity="test",
        idempotency_key="request:v1",
    )
    barrier = Barrier(2)
    results = []

    def authorize(service, owner):
        barrier.wait()
        results.append(
            service.authorize_execution(
                context,
                owner=owner,
                conservative_charge=1.0,
                execution_timeout_seconds=30,
            )
        )

    threads = [
        Thread(target=authorize, args=(first_service, "worker-1")),
        Thread(target=authorize, args=(second_service, "worker-2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    granted = [result for result in results if result.allowed]
    assert len(granted) == 1
    assert granted[0].fencing_token > 0
    assert granted[0].attempt_id
    assert all(
        result.decision.reason in {"authorized", "attempt_in_flight"}
        for result in results
    )

    denied = first_service.authorize_execution(
        ExecutionContext(
            **{
                **context.as_dict(),
                "free_only": True,
                "idempotency_key": "request:free-only",
            }
        ),
        owner="worker-3",
        conservative_charge=1.0,
        execution_timeout_seconds=30,
    )
    assert denied.allowed is False
    assert denied.decision.reason == "free_only"
    assert first_service.repository.paid_attempt_count(ProviderName.BRAVE) == 1


def test_atomic_authorization_counts_settled_charges_against_budget(tmp_path):
    from argus.broker.readiness import ExecutionContext

    service, _ = _service(tmp_path)
    service.register_provider(_registration(budget=2.0))
    _ready(service)
    base = {
        "provider": ProviderName.BRAVE,
        "tier": 1,
        "plan_providers": (ProviderName.BRAVE,),
        "free_only": False,
        "caller_tier_cap": 1,
        "scope": _exact_scope(),
        "plan_id": "budget-plan:v1",
        "caller_identity": "test",
    }
    first = service.authorize_execution(
        ExecutionContext(**base, idempotency_key="budget-request:1"),
        owner="worker-1",
        conservative_charge=1.25,
        execution_timeout_seconds=30,
    )
    assert first.allowed
    service.complete_execution(
        first,
        failure=None,
        actual_charge=1.25,
        charge_known=True,
        evidence_ref="provider:budget:receipt-1",
    )

    projection = service.budget_projection(ProviderName.BRAVE)
    assert projection["argus_estimated_charge"] == 1.25
    assert projection["uncertain_charge"] == 0.0
    assert projection["remaining"] == 0.75

    denied = service.authorize_execution(
        ExecutionContext(**base, idempotency_key="budget-request:2"),
        owner="worker-2",
        conservative_charge=1.0,
        execution_timeout_seconds=30,
    )
    assert denied.allowed is False
    assert denied.decision.reason == "exhausted"


def test_terminal_failure_and_uncertain_charge_materialize_in_completion_transaction(
    tmp_path,
):
    from argus.broker.provider_evidence import FailureCategory, ProviderFailure
    from argus.broker.readiness import ExecutionContext

    service, repository = _service(tmp_path)
    service.register_provider(_registration(provider=ProviderName.SERPER))
    _ready(service, ProviderName.SERPER)
    context = ExecutionContext(
        provider=ProviderName.SERPER,
        tier=3,
        plan_providers=(ProviderName.SERPER,),
        free_only=False,
        caller_tier_cap=3,
        scope=_exact_scope(provider=ProviderName.SERPER),
        plan_id="plan:serper",
        caller_identity="test",
        idempotency_key="request:serper",
    )
    authorization = service.authorize_execution(
        context,
        owner="worker",
        conservative_charge=1.0,
        execution_timeout_seconds=30,
    )
    assert authorization.allowed
    service.complete_execution(
        authorization,
        failure=ProviderFailure(
            category=FailureCategory.BALANCE_EXHAUSTED,
            provider=ProviderName.SERPER,
        ),
        actual_charge=None,
        charge_known=False,
        evidence_ref="provider:402:receipt",
    )

    for mode in ("discovery", "research", "recovery", "grounding"):
        snapshot = service.snapshot(ProviderName.SERPER, request_class=mode)
        assert snapshot.spend == "exhausted"
        assert snapshot.execution_decision.code == "spend_blocked"
    restarted, _ = _service(tmp_path)
    restarted.register_provider(
        _registration(
            provider=ProviderName.SERPER,
            credential="secret-version:v2",
        )
    )
    for mode in ("discovery", "research", "recovery", "grounding"):
        assert (
            restarted.snapshot(ProviderName.SERPER, request_class=mode).spend
            == "exhausted"
        )
    assert (
        repository.emit_operator_alert_once(
            ProviderName.SERPER,
            account_fingerprint="account:v1",
            alert_kind="exhaustion_without_refresh",
        )
        is False
    )

    from argus.persistence.provider_spend import ProviderSpendRepository

    spend = ProviderSpendRepository(repository.session_factory)
    resolved = spend.resolve(
        authorization.attempt_id,
        actual_charge=1.0,
        outcome="balance_exhausted",
        source="operator",
        actor_identity="operator:test",
        idempotency_key="resolve:serper",
    )
    assert resolved.status == "resolved"
    assert (
        service.snapshot_for_scope(
            ProviderName.SERPER,
            _exact_scope(provider=ProviderName.SERPER),
        ).spend
        == "exhausted"
    )


def test_execution_outcome_updates_exact_request_class_only(tmp_path):
    service, _ = _service(tmp_path)
    service.register_provider(_registration())
    research_scope = _exact_scope(request_class="research")

    service.record_legacy_outcome(
        ProviderName.BRAVE,
        egress="local",
        success=True,
        latency_ms=12,
        scope=research_scope,
    )

    research = service.snapshot_for_scope(ProviderName.BRAVE, research_scope)
    discovery = service.snapshot_for_scope(
        ProviderName.BRAVE, _exact_scope(request_class="discovery")
    )
    assert research.usability == "usable"
    assert research.reachability == "reachable"
    assert discovery.usability == "unknown"
    assert discovery.reachability == "unknown"


def test_postgres_ci_persists_sanitized_readiness_evidence_artifact():
    from pathlib import Path

    workflow = Path(".github/workflows/ci.yml").read_text()
    assert "scripts/verify_provider_readiness_postgres.py" in workflow
    assert "scripts/generate_provider_fixture_attestations.py --check" in workflow
    assert "provider-readiness-postgres.json" in workflow
    assert "actions/upload-artifact@" in workflow


def test_architecture_has_no_legacy_semantic_reads_in_decision_or_surfaces():
    from pathlib import Path

    forbidden = (
        ".is_available(",
        ".status(",
        "peek_execution_status(",
        "peek_best_egress(",
        "check_status(",
        "get_remaining_budget(",
        "provider_summary(",
        "get_all_status(",
    )
    files = (
        "argus/broker/execution.py",
        "argus/broker/router.py",
        "argus/api/routes_health.py",
        "argus/api/routes_admin.py",
        "argus/operations/status.py",
        "argus/cli/main.py",
        "argus/development_mcp_resources.py",
        "argus/development_mcp_tools.py",
    )
    violations = {
        path: token
        for path in files
        for token in forbidden
        if token in Path(path).read_text(encoding="utf-8")
    }
    assert violations == {}


def test_real_postgres_atomic_authorization_and_materialized_recovery():
    """Run only against an explicitly disposable, already-migrated database."""
    import os
    from urllib.parse import urlparse

    url = os.environ.get("ARGUS_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("ARGUS_TEST_POSTGRES_URL is not configured")
    from argus.broker.readiness import (
        ExecutionContext,
        ProviderReadinessService,
    )
    from argus.persistence.readiness import create_readiness_repository
    from argus.recovery.operator import validate_scratch_database

    validate_scratch_database(urlparse(url).path.lstrip("/"))
    first = ProviderReadinessService(
        repository=create_readiness_repository(url, create_schema=False)
    )
    first.register_provider(_registration(provider=ProviderName.SEARCHAPI))
    _ready(first, ProviderName.SEARCHAPI)
    second = ProviderReadinessService(
        repository=create_readiness_repository(url, create_schema=False)
    )
    context = ExecutionContext(
        provider=ProviderName.SEARCHAPI,
        tier=3,
        plan_providers=(ProviderName.SEARCHAPI,),
        free_only=False,
        caller_tier_cap=3,
        scope=_exact_scope(provider=ProviderName.SEARCHAPI),
        plan_id="pg-plan:v1",
        caller_identity="pg-test",
        idempotency_key="pg-request:v1",
    )
    barrier = Barrier(2)
    results = []

    def authorize(service, owner):
        barrier.wait()
        results.append(
            service.authorize_execution(
                context,
                owner=owner,
                conservative_charge=1.0,
                execution_timeout_seconds=30,
            )
        )

    workers = [
        Thread(target=authorize, args=(first, "pg-worker-1")),
        Thread(target=authorize, args=(second, "pg-worker-2")),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert sum(result.allowed for result in results) == 1
    assert (
        first.snapshot_for_scope(
            ProviderName.SEARCHAPI,
            _exact_scope(provider=ProviderName.SEARCHAPI),
        ).execution_decision.code
        == "eligible"
    )
