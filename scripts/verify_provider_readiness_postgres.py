#!/usr/bin/env python3
"""Verify readiness fencing/materialization against a disposable PostgreSQL DB."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from threading import Barrier, Thread
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
from argus.providers.fixture_attestation import build_fixture_attestation
from argus.recovery.operator import validate_scratch_database


def main() -> None:
    url = os.environ["ARGUS_TEST_POSTGRES_URL"]
    database = validate_scratch_database(urlparse(url).path.lstrip("/"))
    suffix = uuid.uuid4().hex[:12]
    scope = ReadinessScope(
        egress="local",
        machine=f"pg-ci-{suffix}",
        request_class="discovery",
        release_revision=f"release-{suffix}",
        contract_version="contract-1",
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
    barrier = Barrier(2)
    results = []

    def authorize(
        service: ProviderReadinessService, owner: str, mode: str,
    ) -> None:
        barrier.wait()
        results.append(service.authorize_execution(
            contexts[mode],
            owner=owner,
            conservative_charge=1.0,
            execution_timeout_seconds=30,
        ))

    workers = [
        Thread(target=authorize, args=(first, "pg-worker-1", "discovery")),
        Thread(target=authorize, args=(second, "pg-worker-2", "research")),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
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
    ProviderSpendRepository(first.repository.session_factory).resolve(
        granted[0].attempt_id,
        actual_charge=0.5,
        outcome="success",
        source="operator",
        actor_identity="postgres-ci",
        idempotency_key=f"resolve-{suffix}",
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
    rotated_release = f"release-rotated-{suffix}"
    rotated_fixture_ref, rotated_attestation = build_fixture_attestation(
        ProviderName.SEARCHAPI,
        release=rotated_release,
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
        release_revision=rotated_release,
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
    print(json.dumps({
        "database": database,
        "granted": len(granted),
        "fencing_token": granted[0].fencing_token,
        "fixture_probe": fixture_probe.reason,
        "live_probe_consumed": True,
        "reconciled_attempt": True,
        "terminal_modes_after_credential_rotation": terminal_modes,
        "snapshot_generation": snapshot.generation,
        "status": "ok",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
