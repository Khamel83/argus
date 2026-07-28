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
            machine="test-machine",
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
    assert len(overflow.evidence_receipts) == 1


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
        spend_reserved=True,
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
        spend_reserved=True,
    )
    with pytest.raises(ReadinessConflict, match="already consumed"):
        repository.consume_probe_once(
            provider=ProviderName.BRAVE,
            idempotency_key="probe:brave:1",
            durable_receipt="receipt:brave:1",
            spend_reserved=True,
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


def _exact_scope(
    *,
    provider: ProviderName = ProviderName.BRAVE,
    account: str = "account:v1",
    config: str = "config:v1",
    credential: str = "secret-version:v1",
    egress: str = "local",
    machine: str = "test-machine",
    request_class: str = "discovery",
    release: str = "release-1",
    contract: str = "contract-1",
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
    release: str | None = "release-1",
    contract: str | None = "contract-1",
    fixture: str | None = "attestation:manifest:v1",
):
    from argus.broker.readiness import ProviderRegistrationSpec

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
        fixture_attestation=(
            {
                "release": release,
                "adapter_code_sha256": "adapter-sha",
                "fixture_manifest_sha256": "manifest-sha",
                "request_contract": "search-request-v1",
                "response_contract": "provider-batch-v1",
                "provider_contract": contract,
            }
            if fixture and release and contract else None
        ),
    )


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
    restarted = ProviderReadinessService(
        repository=create_readiness_repository(url)
    )
    assert restarted.registration(ProviderName.BRAVE).issues == decision.issues


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
        assert repository.get_snapshot(
            ProviderName.BRAVE, scope.fingerprint()
        ) is not None
        rendered = service.snapshot_for_scope(ProviderName.BRAVE, scope)
        assert rendered.compatibility == "compatible"
        assert rendered.reachability == "unknown"
        assert rendered.execution_decision.code == "unavailable"


def test_protected_evidence_overflow_is_bounded_and_unavailable(tmp_path):
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

    snapshot = service.snapshot_for_scope(
        ProviderName.SERPER, _exact_scope(provider=ProviderName.SERPER)
    )
    assert snapshot.spend == "exhausted"
    assert snapshot.execution_decision.code == "spend_blocked"
    assert repository.emit_operator_alert_once(
        ProviderName.SERPER,
        account_fingerprint="account:v1",
        alert_kind="exhaustion_without_refresh",
    ) is False

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
    assert service.snapshot_for_scope(
        ProviderName.SERPER,
        _exact_scope(provider=ProviderName.SERPER),
    ).spend == "exhausted"


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
        "argus/mcp/resources.py",
        "argus/mcp/tools.py",
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
        results.append(service.authorize_execution(
            context, owner=owner, conservative_charge=1.0,
            execution_timeout_seconds=30,
        ))

    workers = [
        Thread(target=authorize, args=(first, "pg-worker-1")),
        Thread(target=authorize, args=(second, "pg-worker-2")),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert sum(result.allowed for result in results) == 1
    assert first.snapshot_for_scope(
        ProviderName.SEARCHAPI,
        _exact_scope(provider=ProviderName.SEARCHAPI),
    ).execution_decision.code == "eligible"
