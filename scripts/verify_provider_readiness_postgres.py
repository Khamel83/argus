#!/usr/bin/env python3
"""Verify readiness fencing/materialization against a disposable PostgreSQL DB."""

from __future__ import annotations

import json
import os
import uuid
from argparse import ArgumentParser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Empty, Queue
from threading import Barrier, Event, Thread, current_thread
from unittest.mock import patch
from urllib.parse import urlparse

from argus.broker.readiness import (
    ExecutionContext,
    ProbeAuthorization,
    ProviderObservation,
    ProviderReadinessService,
    ProviderRegistrationSpec,
    ReadinessScope,
)
from argus.models import ProviderName
from argus.persistence.readiness import (
    ProviderReadinessRepository,
    create_readiness_repository,
)
from argus.persistence.provider_spend import (
    BudgetExhaustedError,
    ProviderSpendRepository,
)
from argus.providers.fixture_attestation import (
    _manifest_path,
    build_fixture_attestation,
    default_release_revision,
)
from argus.recovery.operator import validate_scratch_database


def authorize_workers(worker_specs, contexts):
    """Run the exact worker set and fail closed on crashes or incomplete joins."""
    expected = len(worker_specs)
    if expected < 1:
        raise RuntimeError("authorization worker set must not be empty")
    barrier = Barrier(expected)
    outcomes = Queue()

    def authorize(service, owner, mode):
        try:
            barrier.wait(timeout=10)
            result = service.authorize_execution(
                contexts[mode],
                owner=owner,
                conservative_charge=1.0,
                execution_timeout_seconds=30,
            )
            outcomes.put({
                "ok": True,
                "mode": mode,
                "owner": owner,
                "result": result,
            })
        except BaseException as error:
            outcomes.put({
                "ok": False,
                "mode": mode,
                "owner": owner,
                "error_type": type(error).__name__,
            })

    workers = [
        Thread(
            target=authorize,
            args=spec,
            name=f"readiness-verifier-{spec[2]}",
            daemon=True,
        )
        for spec in worker_specs
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    alive = [worker.name for worker in workers if worker.is_alive()]
    if alive:
        raise RuntimeError("authorization worker did not exit")
    records = []
    while True:
        try:
            records.append(outcomes.get_nowait())
        except Empty:
            break
    if len(records) != expected:
        raise RuntimeError("authorization worker result count mismatch")
    failures = [record for record in records if not record["ok"]]
    if failures:
        kinds = ",".join(sorted(record["error_type"] for record in failures))
        raise RuntimeError(f"authorization worker failed: {kinds}")
    return [record["result"] for record in records]


def verify_settle_authorize_race(first, second, contexts, suffix):
    """Prove settlement commits uncertainty before a waiting cross-mode reserve."""
    discovery = ExecutionContext(**{
        **contexts["discovery"].as_dict(),
        "plan_id": f"pg-race-discovery-{suffix}",
        "idempotency_key": f"pg-race-discovery-{suffix}",
    })
    research = ExecutionContext(**{
        **contexts["research"].as_dict(),
        "plan_id": f"pg-race-research-{suffix}",
        "idempotency_key": f"pg-race-research-{suffix}",
    })
    authorization = first.authorize_execution(
        discovery,
        owner="pg-race-settler",
        conservative_charge=0.25,
        execution_timeout_seconds=30,
    )
    if not authorization.allowed:
        raise RuntimeError("settle race fixture could not reserve discovery")
    lock_acquired = Event()
    authorize_lock_attempted = Event()
    release_settlement = Event()
    outcomes = Queue()
    original_lock = first.repository._lock_provider_budget
    original_authorize_lock = second.repository._lock_provider_budget

    def controlled_lock(session, provider):
        original_lock(session, provider)
        if current_thread().name == "readiness-race-settle":
            lock_acquired.set()
            if not release_settlement.wait(timeout=10):
                raise RuntimeError("settlement race release timed out")

    first.repository._lock_provider_budget = controlled_lock

    def observed_authorize_lock(session, provider):
        authorize_lock_attempted.set()
        original_authorize_lock(session, provider)

    second.repository._lock_provider_budget = observed_authorize_lock

    def settle():
        try:
            first.complete_execution(
                authorization,
                failure=None,
                actual_charge=None,
                charge_known=False,
                evidence_ref=f"pg-race-uncertain-{suffix}",
            )
            outcomes.put(("settle", True, None))
        except BaseException as error:
            outcomes.put(("settle", False, type(error).__name__))

    def authorize():
        try:
            decision = second.authorize_execution(
                research,
                owner="pg-race-authorizer",
                conservative_charge=0.01,
                execution_timeout_seconds=30,
            )
            outcomes.put(("authorize", True, decision))
        except BaseException as error:
            outcomes.put(("authorize", False, type(error).__name__))

    workers = [
        Thread(target=settle, name="readiness-race-settle", daemon=True),
        Thread(target=authorize, name="readiness-race-authorize", daemon=True),
    ]
    workers[0].start()
    if not lock_acquired.wait(timeout=10):
        raise RuntimeError("settlement did not acquire provider budget lock")
    workers[1].start()
    if not authorize_lock_attempted.wait(timeout=10):
        raise RuntimeError("cross-mode authorization did not attempt budget lock")
    release_settlement.set()
    for worker in workers:
        worker.join(timeout=30)
    if any(worker.is_alive() for worker in workers):
        raise RuntimeError("settle race worker did not exit")
    records = [outcomes.get_nowait() for _ in range(2)]
    if any(not record[1] for record in records):
        raise RuntimeError("settle race worker failed")
    decision = next(record[2] for record in records if record[0] == "authorize")
    if decision.allowed or decision.decision.reason != "uncertain":
        raise RuntimeError("cross-mode reserve escaped uncertain settlement")
    ProviderSpendRepository(first.repository.session_factory).resolve(
        authorization.attempt_id,
        actual_charge=0.0,
        outcome="confirmed_not_consumed",
        source="operator",
        actor_identity="postgres-ci",
        idempotency_key=f"pg-race-resolve-{suffix}",
    )
    return True


def verify_exact_expiry_boundary_rejections(repository, scope, suffix):
    """Prove forged exact-expiry writes are rejected without durable changes."""
    database_now = repository.authority_now()

    def terminal(producer, expires_at):
        return ProviderObservation(
            provider=ProviderName.SEARCHAPI,
            dimension="spend",
            state="exhausted",
            source="provider_authoritative",
            scope=scope,
            observed_at=producer,
            ttl_seconds=None,
            evidence_ref=f"pg-forged-terminal-{suffix}",
            protected=True,
            expires_at=expires_at,
        )

    future_producer = database_now + timedelta(seconds=20)
    cases = []

    naive_producer = terminal(
        database_now - timedelta(days=1), database_now + timedelta(days=1)
    )
    object.__setattr__(
        naive_producer,
        "observed_at",
        naive_producer.observed_at.replace(tzinfo=None),
    )
    cases.append(naive_producer)

    naive_expiry = terminal(future_producer, future_producer + timedelta(days=1))
    object.__setattr__(
        naive_expiry,
        "expires_at",
        naive_expiry.expires_at.replace(tzinfo=None),
    )
    cases.append(naive_expiry)

    ttl_and_expiry = terminal(
        future_producer, future_producer + timedelta(days=1)
    )
    object.__setattr__(ttl_and_expiry, "ttl_seconds", 1)
    cases.append(ttl_and_expiry)

    equal_expiry = terminal(
        future_producer, future_producer + timedelta(days=1)
    )
    object.__setattr__(equal_expiry, "expires_at", future_producer)
    cases.append(equal_expiry)

    earlier_expiry = terminal(
        future_producer, future_producer + timedelta(days=1)
    )
    object.__setattr__(
        earlier_expiry,
        "expires_at",
        future_producer - timedelta(microseconds=1),
    )
    cases.append(earlier_expiry)

    oversized_producer = database_now - timedelta(days=1)
    too_long = terminal(
        oversized_producer, oversized_producer + timedelta(days=364)
    )
    object.__setattr__(
        too_long,
        "expires_at",
        oversized_producer + timedelta(days=365, microseconds=1),
    )
    cases.append(too_long)

    before_observations = repository.observations(ProviderName.SEARCHAPI)
    before_snapshot = repository.read_snapshot(
        ProviderName.SEARCHAPI, scope.fingerprint()
    )
    for observation in cases:
        try:
            repository.record_and_materialize(
                observation,
                lambda *_args: {"forged": True},
                _allow_exact_expiry=True,
            )
        except ValueError:
            pass
        else:
            raise RuntimeError("forged exact expiry write was accepted")
        if repository.observations(ProviderName.SEARCHAPI) != before_observations:
            raise RuntimeError("forged exact expiry write changed observations")
        if (
            repository.read_snapshot(ProviderName.SEARCHAPI, scope.fingerprint())
            != before_snapshot
        ):
            raise RuntimeError("forged exact expiry write changed snapshot")
    return True


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--inject-worker-crash", action="store_true")
    args = parser.parse_args()
    url = os.environ["ARGUS_TEST_POSTGRES_URL"]
    validate_scratch_database(urlparse(url).path.lstrip("/"))
    suffix = uuid.uuid4().hex[:12]
    contract_version = json.loads(
        _manifest_path().read_bytes()
    )["providers"][ProviderName.SEARCHAPI.value]["contract_version"]
    scope = ReadinessScope(
        egress="local",
        machine=f"pg-ci-{suffix}",
        request_class="discovery",
        release_revision=default_release_revision(),
        contract_version=contract_version,
        configuration_fingerprint=f"config-{suffix}",
        credential_version_fingerprint=f"credential-{suffix}",
        account_fingerprint=f"account-{suffix}",
    )
    first = ProviderReadinessService(
        repository=create_readiness_repository(url, create_schema=False)
    )
    fixture_ref, fixture_attestation = build_fixture_attestation(
        ProviderName.SEARCHAPI,
        release=scope.release_revision,
        provider_contract=scope.contract_version,
    )
    first.register_provider(ProviderRegistrationSpec(
        provider=ProviderName.SEARCHAPI,
        enabled=True,
        configuration_fingerprint=scope.configuration_fingerprint,
        credential_version_fingerprint=scope.credential_version_fingerprint,
        account_fingerprint=scope.account_fingerprint,
        budget_limit=1.5,
        durable_spend_repository=True,
        release_revision=scope.release_revision,
        contract_version=scope.contract_version,
        fixture_evidence_ref=fixture_ref,
        fixture_attestation=fixture_attestation,
        machine=scope.machine,
    ))
    now = datetime.now(timezone.utc)
    for dimension, state in (
        ("reachability", "reachable"),
        ("usability", "usable"),
        ("cooldown", "clear"),
        ("spend", "available"),
    ):
        first.record_observation(ProviderObservation(
            provider=ProviderName.SEARCHAPI,
            dimension=dimension,
            state=state,
            source="postgres_ci",
            scope=scope,
            observed_at=now,
            ttl_seconds=300,
            evidence_ref=f"{dimension}-{suffix}",
        ))
    exact_expiry_zero_writes = verify_exact_expiry_boundary_rejections(
        first.repository, scope, suffix
    )
    second = ProviderReadinessService(
        repository=create_readiness_repository(url, create_schema=False)
    )
    contexts = {
        mode: ExecutionContext(
            provider=ProviderName.SEARCHAPI,
            tier=3,
            plan_providers=(ProviderName.SEARCHAPI,),
            free_only=False,
            caller_tier_cap=3,
            scope=first.execution_scope(
                ProviderName.SEARCHAPI,
                egress="local",
                request_class=mode,
            ),
            plan_id=f"pg-plan-{mode}-{suffix}",
            caller_identity="postgres-ci",
            idempotency_key=f"pg-request-{mode}-{suffix}",
        )
        for mode in ("discovery", "research")
    }
    worker_two = second
    if args.inject_worker_crash:
        class CrashingWorker:
            def authorize_execution(self, *_args, **_kwargs):
                raise RuntimeError("injected verifier worker crash")

        worker_two = CrashingWorker()
    results = authorize_workers(
        (
            (first, "pg-worker-1", "discovery"),
            (worker_two, "pg-worker-2", "research"),
        ),
        contexts,
    )
    granted = [result for result in results if result.allowed]
    if len(granted) != 1:
        raise RuntimeError("PostgreSQL fencing did not grant exactly one attempt")

    first.complete_execution(
        granted[0],
        failure=None,
        actual_charge=None,
        charge_known=False,
        evidence_ref=f"uncertain-{suffix}",
    )
    first.complete_execution(
        granted[0],
        failure=None,
        actual_charge=0.5,
        charge_known=True,
        evidence_ref=f"matched-settlement-{suffix}",
    )
    matched_modes = [
        first.snapshot(
            ProviderName.SEARCHAPI,
            request_class=mode,
        ).spend
        for mode in ("discovery", "research", "recovery", "grounding")
    ]
    if matched_modes != ["available"] * 4:
        raise RuntimeError(
            "matched settlement did not clear account uncertainty"
        )
    legacy_spend = ProviderSpendRepository(first.repository.session_factory)
    direct_reservation = legacy_spend.reserve(
        provider=ProviderName.SEARCHAPI,
        conservative_charge=0.1,
        budget_limit=1.5,
        caller_identity="postgres-ci-legacy",
        caller_label="direct-settle",
        idempotency_key=f"pg-direct-reserve-{suffix}",
    )
    direct_uncertain_modes = [
        first.snapshot(
            ProviderName.SEARCHAPI,
            request_class=mode,
        ).spend
        for mode in ("discovery", "research", "recovery", "grounding")
    ]
    if direct_uncertain_modes != ["uncertain"] * 4:
        raise RuntimeError("direct reserve did not protect account uncertainty")
    legacy_spend.settle(
        direct_reservation.attempt_id,
        actual_charge=0.05,
        outcome="success",
    )
    direct_settlement_modes = [
        first.snapshot(
            ProviderName.SEARCHAPI,
            request_class=mode,
        ).spend
        for mode in ("discovery", "research", "recovery", "grounding")
    ]
    if direct_settlement_modes != ["available"] * 4:
        raise RuntimeError(
            "direct settlement did not reconcile account uncertainty"
        )
    settle_race_blocked = verify_settle_authorize_race(
        first, second, contexts, suffix
    )
    fixture_probe = first.authorize_probe(ProviderName.SEARCHAPI, "fixture")
    if not fixture_probe.allowed:
        raise RuntimeError("attested fixture round trip was not authorized")
    probe_key = f"pg-probe-{suffix}"
    probe_receipt = f"pg-probe-receipt-{suffix}"
    probe = first.authorize_probe(
        ProviderName.SEARCHAPI,
        "billable_search",
        ProbeAuthorization(
            workflow="explicit_validation",
            provider=ProviderName.SEARCHAPI,
            idempotency_key=probe_key,
            durable_receipt=probe_receipt,
            conservative_charge=0.25,
        ),
    )
    if not probe.allowed or not probe.authorization_id:
        raise RuntimeError("durable live-probe authorization was not persisted")
    probe_execution = first.repository.consume_probe_once(
        provider=ProviderName.SEARCHAPI,
        idempotency_key=probe_key,
        durable_receipt=probe_receipt,
        attempt_id=probe.attempt_id,
    )
    first.complete_execution(
        probe_execution,
        failure=None,
        actual_charge=0.0,
        charge_known=True,
        evidence_ref=f"probe-result-{suffix}",
        probe_idempotency_key=probe_key,
    )

    request_modes = ("discovery", "research", "recovery", "grounding")
    oversized_ref = f"recurring-oversized-{suffix}"
    try:
        first.repository.record_terminal_exhaustion(
            provider=ProviderName.SEARCHAPI,
            account_fingerprint=scope.account_fingerprint,
            recurring=True,
            reset_at=(
                first.repository.authority_now() + timedelta(days=365 * 100)
            ),
            evidence_ref=oversized_ref,
        )
    except ValueError:
        pass
    else:
        raise RuntimeError("100-year recurring reset was accepted")
    if any(
        row.evidence_ref == oversized_ref
        for row in first.repository.observations(ProviderName.SEARCHAPI)
    ):
        raise RuntimeError("oversized recurring reset wrote partial evidence")

    fault_ref = f"recurring-fault-{suffix}"
    fault_before = [
        first.snapshot(
            ProviderName.SEARCHAPI,
            request_class=mode,
        ).as_dict()
        for mode in request_modes
    ]
    original_put_snapshot = first.repository._put_snapshot
    fault_snapshot_writes = 0

    def fail_during_second_terminal_scope(*put_args, **put_kwargs):
        nonlocal fault_snapshot_writes
        original_put_snapshot(*put_args, **put_kwargs)
        fault_snapshot_writes += 1
        if fault_snapshot_writes == 2:
            raise RuntimeError("injected PostgreSQL terminal fanout fault")

    with patch.object(
        first.repository,
        "_put_snapshot",
        side_effect=fail_during_second_terminal_scope,
    ):
        try:
            first.repository.record_terminal_exhaustion(
                provider=ProviderName.SEARCHAPI,
                account_fingerprint=scope.account_fingerprint,
                recurring=True,
                reset_at=first.repository.authority_now() + timedelta(hours=1),
                evidence_ref=fault_ref,
            )
        except RuntimeError as error:
            if str(error) != "injected PostgreSQL terminal fanout fault":
                raise
        else:
            raise RuntimeError("terminal fanout fault was not injected")
    if any(
        row.evidence_ref == fault_ref
        for row in first.repository.observations(ProviderName.SEARCHAPI)
    ):
        raise RuntimeError("terminal fanout fault committed partial evidence")
    fault_after = [
        first.snapshot(
            ProviderName.SEARCHAPI,
            request_class=mode,
        ).as_dict()
        for mode in request_modes
    ]
    if fault_after != fault_before:
        raise RuntimeError("terminal fanout fault committed partial snapshots")

    recurring_ref = f"recurring-terminal-{suffix}"
    recurring_database_now = first.repository.authority_now().replace(
        microsecond=654320
    )
    recurring_reset_at = recurring_database_now + timedelta(microseconds=1)
    with patch.object(
        first.repository,
        "authority_now",
        side_effect=(recurring_database_now, recurring_reset_at),
    ) as recurring_clock:
        first.repository.record_terminal_exhaustion(
            provider=ProviderName.SEARCHAPI,
            account_fingerprint=scope.account_fingerprint,
            recurring=True,
            reset_at=recurring_reset_at,
            evidence_ref=recurring_ref,
        )
    if recurring_clock.call_count != 1:
        raise RuntimeError("terminal fanout read database time more than once")
    recurring_rows = [
        row for row in first.repository.observations(ProviderName.SEARCHAPI)
        if (
            row.dimension == "spend"
            and row.evidence_ref == recurring_ref
        )
    ]
    if (
        len(recurring_rows) != 4
        or {row.expires_at for row in recurring_rows} != {recurring_reset_at}
    ):
        raise RuntimeError("recurring exhaustion lost exact reset precision")
    with patch.object(
        ProviderReadinessRepository,
        "authority_now",
        return_value=recurring_reset_at - timedelta(microseconds=1),
    ):
        try:
            legacy_spend.reserve(
                provider=ProviderName.SEARCHAPI,
                conservative_charge=0.01,
                budget_limit=1.5,
                caller_identity="postgres-ci-legacy",
                caller_label="recurring-terminal-boundary",
                idempotency_key=f"pg-recurring-terminal-denied-{suffix}",
            )
        except BudgetExhaustedError:
            pass
        else:
            raise RuntimeError(
                "recurring exhaustion allowed a reserve before exact reset"
            )
    with patch.object(
        ProviderReadinessRepository,
        "authority_now",
        return_value=recurring_reset_at,
    ):
        reset_reservation = legacy_spend.reserve(
            provider=ProviderName.SEARCHAPI,
            conservative_charge=0.01,
            budget_limit=1.5,
            caller_identity="postgres-ci-legacy",
            caller_label="recurring-terminal-boundary",
            idempotency_key=f"pg-recurring-terminal-reset-{suffix}",
        )
    recurring_reset_modes = [
        first.snapshot(
            ProviderName.SEARCHAPI,
            request_class=mode,
        ).spend
        for mode in request_modes
    ]
    if recurring_reset_modes != ["uncertain"] * 4:
        raise RuntimeError(
            "reset reservation did not atomically materialize account uncertainty"
        )
    legacy_spend.settle(
        reset_reservation.attempt_id,
        actual_charge=0.0,
        outcome="success",
    )

    pre_terminal_reservation = legacy_spend.reserve(
        provider=ProviderName.SEARCHAPI,
        conservative_charge=0.1,
        budget_limit=1.5,
        caller_identity="postgres-ci-legacy",
        caller_label="terminal-precedence",
        idempotency_key=f"pg-pre-terminal-{suffix}",
    )
    first.repository.record_terminal_exhaustion(
        provider=ProviderName.SEARCHAPI,
        account_fingerprint=scope.account_fingerprint,
        recurring=False,
        reset_at=None,
        evidence_ref=f"terminal-{suffix}",
    )
    try:
        legacy_spend.reserve(
            provider=ProviderName.SEARCHAPI,
            conservative_charge=0.01,
            budget_limit=1.5,
            caller_identity="postgres-ci-legacy",
            caller_label="terminal-precedence",
            idempotency_key=f"pg-terminal-denied-{suffix}",
        )
    except BudgetExhaustedError:
        pass
    else:
        raise RuntimeError("terminal exhaustion allowed a direct reservation")
    legacy_spend.settle(
        pre_terminal_reservation.attempt_id,
        actual_charge=0.05,
        outcome="success",
    )
    terminal_precedence_modes = [
        first.snapshot(
            ProviderName.SEARCHAPI,
            request_class=mode,
        ).spend
        for mode in ("discovery", "research", "recovery", "grounding")
    ]
    if terminal_precedence_modes != ["exhausted"] * 4:
        raise RuntimeError("ordinary settlement cleared terminal exhaustion")
    rotated_fixture_ref, rotated_attestation = build_fixture_attestation(
        ProviderName.SEARCHAPI,
        release=scope.release_revision,
        provider_contract=scope.contract_version,
    )
    first.register_provider(ProviderRegistrationSpec(
        provider=ProviderName.SEARCHAPI,
        enabled=True,
        configuration_fingerprint=f"config-rotated-{suffix}",
        credential_version_fingerprint=f"credential-rotated-{suffix}",
        account_fingerprint=scope.account_fingerprint,
        budget_limit=1.5,
        durable_spend_repository=True,
        release_revision=scope.release_revision,
        contract_version=scope.contract_version,
        fixture_evidence_ref=rotated_fixture_ref,
        fixture_attestation=rotated_attestation,
        machine=scope.machine,
    ))
    restarted = ProviderReadinessService(
        repository=create_readiness_repository(url, create_schema=False)
    )
    terminal_modes = [
        restarted.snapshot(
            ProviderName.SEARCHAPI,
            request_class=mode,
        ).spend
        for mode in ("discovery", "research", "recovery", "grounding")
    ]
    if terminal_modes != ["exhausted"] * 4:
        raise RuntimeError(
            "terminal account spend did not survive credential rotation"
        )
    snapshot = restarted.snapshot(
        ProviderName.SEARCHAPI,
        request_class="discovery",
    )
    if snapshot.execution_decision.code != "spend_blocked":
        raise RuntimeError("materialized terminal snapshot was not recovered")
    summary = {
        "schema": "argus-provider-readiness-postgres-v1",
        "scenarios": {
            "account_lock_grants": len(granted),
            "attested_fixture_probe_authorized": fixture_probe.allowed,
            "billable_probe_consumed": True,
            "direct_settlement_modes": direct_settlement_modes,
            "exact_expiry_zero_writes": exact_expiry_zero_writes,
            "recurring_atomic_rollback": True,
            "recurring_reset_modes": recurring_reset_modes,
            "recurring_single_db_now_calls": recurring_clock.call_count,
            "settle_authorize_race_blocked": settle_race_blocked,
            "terminal_precedence_modes": terminal_precedence_modes,
            "matched_uncertainty_modes": matched_modes,
            "terminal_account_modes": terminal_modes,
        },
        "status": "ok",
    }
    encoded = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n")
    print(encoded)


if __name__ == "__main__":
    main()
