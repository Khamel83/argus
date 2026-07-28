#!/usr/bin/env python3
"""Verify readiness fencing/materialization against a disposable PostgreSQL DB."""

from __future__ import annotations

import json
import os
import uuid
from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from threading import Barrier, Event, Thread, current_thread
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
from argus.persistence.readiness import create_readiness_repository
from argus.persistence.provider_spend import ProviderSpendRepository
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

    first.repository.record_terminal_exhaustion(
        provider=ProviderName.SEARCHAPI,
        account_fingerprint=scope.account_fingerprint,
        recurring=False,
        reset_at=None,
        evidence_ref=f"terminal-{suffix}",
    )
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
            "settle_authorize_race_blocked": settle_race_blocked,
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
