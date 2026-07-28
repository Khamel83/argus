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
    first.register_provider(ProviderRegistrationSpec(
        provider=ProviderName.SEARCHAPI,
        enabled=True,
        configuration_fingerprint=scope.configuration_fingerprint,
        credential_version_fingerprint=scope.credential_version_fingerprint,
        account_fingerprint=scope.account_fingerprint,
        budget_limit=100.0,
        durable_spend_repository=True,
        release_revision=scope.release_revision,
        contract_version=scope.contract_version,
        fixture_evidence_ref=f"attestation:{suffix}",
        fixture_attestation={
            "release": scope.release_revision,
            "adapter_code_sha256": f"adapter-{suffix}",
            "fixture_manifest_sha256": f"manifest-{suffix}",
            "request_contract": "search-request-v1",
            "response_contract": "provider-batch-v1",
            "provider_contract": scope.contract_version,
        },
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
    context = ExecutionContext(
        provider=ProviderName.SEARCHAPI,
        tier=3,
        plan_providers=(ProviderName.SEARCHAPI,),
        free_only=False,
        caller_tier_cap=3,
        scope=scope,
        plan_id=f"pg-plan-{suffix}",
        caller_identity="postgres-ci",
        idempotency_key=f"pg-request-{suffix}",
    )
    barrier = Barrier(2)
    results = []

    def authorize(service: ProviderReadinessService, owner: str) -> None:
        barrier.wait()
        results.append(service.authorize_execution(
            context,
            owner=owner,
            conservative_charge=1.0,
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
    granted = [result for result in results if result.allowed]
    if len(granted) != 1:
        raise RuntimeError("PostgreSQL fencing did not grant exactly one attempt")

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
            spend_reserved=True,
        ),
    )
    if not probe.allowed or not probe.authorization_id:
        raise RuntimeError("durable live-probe authorization was not persisted")
    first.repository.consume_probe_once(
        provider=ProviderName.SEARCHAPI,
        idempotency_key=probe_key,
        durable_receipt=probe_receipt,
        spend_reserved=True,
    )

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
    snapshot = first.snapshot_for_scope(ProviderName.SEARCHAPI, scope)
    if snapshot.execution_decision.code != "eligible":
        raise RuntimeError("materialized readiness snapshot was not recovered")
    print(json.dumps({
        "database": database,
        "granted": len(granted),
        "fencing_token": granted[0].fencing_token,
        "fixture_probe": fixture_probe.reason,
        "live_probe_consumed": True,
        "reconciled_attempt": True,
        "snapshot_generation": snapshot.generation,
        "status": "ok",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
