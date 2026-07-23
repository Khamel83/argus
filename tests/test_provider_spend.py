"""Crash-safe provider spending and scoped caller identity contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from argus.models import ProviderName, ProviderTrace, SearchQuery


def _repository(tmp_path):
    from argus.persistence.provider_spend import create_provider_spend_repository

    return create_provider_spend_repository(
        f"sqlite:///{tmp_path / 'spend.db'}",
        create_schema=True,
    )


def test_paid_attempt_reserves_before_external_work_and_settles_atomically(tmp_path):
    repository = _repository(tmp_path)
    external_observations = []

    reservation = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=10.0,
        caller_identity="maya",
        caller_label="research-ui",
        idempotency_key="run-1:brave:0",
    )
    external_observations.append(repository.get_attempt(reservation.attempt_id).status)

    settled = repository.settle(
        reservation.attempt_id,
        actual_charge=0.25,
        outcome="success",
    )

    assert external_observations == ["uncertain"]
    assert settled.status == "settled"
    assert settled.reserved_charge == 1.0
    assert settled.actual_charge == 0.25
    assert settled.caller_identity == "maya"
    assert settled.caller_label == "research-ui"
    assert repository.provider_summary(ProviderName.BRAVE, budget_limit=10.0) == {
        "provider": "brave",
        "budget_limit": 10.0,
        "argus_estimated_charge": 0.25,
        "uncertain_charge": 0.0,
        "remaining": 9.75,
        "estimate_source": "argus",
        "provider_snapshot": None,
    }


def test_unknown_outcome_never_expires_or_refunds_automatically(tmp_path):
    repository = _repository(tmp_path)
    reservation = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=1.0,
        caller_identity="maya",
        caller_label="",
        idempotency_key="crashed-attempt",
        created_at=datetime.now(tz=None) - timedelta(days=400),
    )

    assert repository.get_attempt(reservation.attempt_id).status == "uncertain"
    assert repository.provider_summary(ProviderName.BRAVE, budget_limit=1.0)[
        "remaining"
    ] == 0.0
    with pytest.raises(Exception, match="budget exhausted"):
        repository.reserve(
            provider=ProviderName.BRAVE,
            conservative_charge=1.0,
            budget_limit=1.0,
            caller_identity="maya",
            caller_label="",
            idempotency_key="later-attempt",
        )


def test_reservation_is_idempotent_and_conflicting_reuse_is_rejected(tmp_path):
    from argus.persistence.provider_spend import SpendConflictError

    repository = _repository(tmp_path)
    kwargs = dict(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=10.0,
        caller_identity="maya",
        caller_label="label",
        idempotency_key="same-attempt",
    )

    first = repository.reserve(**kwargs)
    second = repository.reserve(**kwargs)

    assert second == first
    with pytest.raises(SpendConflictError):
        repository.reserve(**{**kwargs, "conservative_charge": 2.0})


def test_settlement_fault_rolls_back_to_conservative_uncertain_charge(tmp_path):
    repository = _repository(tmp_path)
    reservation = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=10.0,
        caller_identity="maya",
        caller_label="",
        idempotency_key="settlement-fault",
    )

    def fail(stage):
        if stage == "after_settlement_update":
            raise RuntimeError("injected crash")

    with pytest.raises(RuntimeError, match="injected crash"):
        repository.settle(
            reservation.attempt_id,
            actual_charge=0.1,
            outcome="success",
            fault_hook=fail,
        )

    attempt = repository.get_attempt(reservation.attempt_id)
    assert attempt.status == "uncertain"
    assert attempt.actual_charge is None


def test_crash_during_reservation_never_exposes_an_uncommitted_attempt(tmp_path):
    from argus.persistence.provider_spend import ProviderSpendAttemptRow

    repository = _repository(tmp_path)

    def fail(stage):
        if stage == "after_reservation_write":
            raise RuntimeError("crash before external attempt")

    with pytest.raises(RuntimeError, match="before external attempt"):
        repository.reserve(
            provider=ProviderName.BRAVE,
            conservative_charge=1.0,
            budget_limit=10.0,
            caller_identity="maya",
            caller_label="",
            idempotency_key="before-attempt",
            fault_hook=fail,
        )

    with repository.session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(ProviderSpendAttemptRow))
            == 0
        )


def test_operator_resolution_is_idempotent_audited_and_conflict_checked(tmp_path):
    from argus.persistence.provider_spend import SpendConflictError, SpendAuditRow

    repository = _repository(tmp_path)
    reservation = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=10.0,
        caller_identity="maya",
        caller_label="",
        idempotency_key="operator-resolution",
    )

    first = repository.resolve(
        reservation.attempt_id,
        actual_charge=0.0,
        outcome="confirmed_not_consumed",
        source="operator",
        actor_identity="admin",
        idempotency_key="ticket-123",
    )
    second = repository.resolve(
        reservation.attempt_id,
        actual_charge=0.0,
        outcome="confirmed_not_consumed",
        source="operator",
        actor_identity="admin",
        idempotency_key="ticket-123",
    )

    assert second == first
    assert first.status == "resolved"
    with repository.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SpendAuditRow)) == 2
    with pytest.raises(SpendConflictError):
        repository.resolve(
            reservation.attempt_id,
            actual_charge=1.0,
            outcome="charged",
            source="operator",
            actor_identity="admin",
            idempotency_key="ticket-123",
        )


def test_authoritative_reconciliation_records_snapshot_freshness_and_is_idempotent(
    tmp_path,
):
    repository = _repository(tmp_path)
    reservation = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=2000.0,
        caller_identity="maya",
        caller_label="",
        idempotency_key="snapshot-attempt",
    )
    observed_at = datetime.now(tz=None)

    first = repository.record_provider_snapshot(
        provider=ProviderName.BRAVE,
        balance=1777.0,
        observed_at=observed_at,
        actor_identity="provider:brave",
        idempotency_key="brave-snapshot-2026-07-23",
        provider_reference="brave-event-1",
        related_attempt_id=reservation.attempt_id,
        authoritative_charge=1.0,
    )
    second = repository.record_provider_snapshot(
        provider=ProviderName.BRAVE,
        balance=1777.0,
        observed_at=observed_at,
        actor_identity="provider:brave",
        idempotency_key="brave-snapshot-2026-07-23",
        provider_reference="brave-event-1",
        related_attempt_id=reservation.attempt_id,
        authoritative_charge=1.0,
    )

    assert second == first
    summary = repository.provider_summary(ProviderName.BRAVE, budget_limit=2000.0)
    assert summary["estimate_source"] == "argus"
    assert summary["provider_snapshot"] == {
        "balance": 1777.0,
        "source": "provider",
        "observed_at": observed_at.isoformat(),
        "provider_reference": "brave-event-1",
        "related_attempt_id": reservation.attempt_id,
        "authoritative_charge": 1.0,
    }


def test_reconciliation_fault_rolls_back_attempt_and_audit(tmp_path):
    from argus.persistence.provider_spend import SpendAuditRow

    repository = _repository(tmp_path)
    reservation = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=10.0,
        caller_identity="maya",
        caller_label="",
        idempotency_key="reconcile-fault",
    )
    snapshot = repository.record_provider_snapshot(
        provider=ProviderName.BRAVE,
        balance=9.0,
        observed_at=datetime.now(tz=None),
        actor_identity="provider:brave",
        idempotency_key="reconcile-fault-snapshot",
        provider_reference="brave-reconcile-fault",
        related_attempt_id=reservation.attempt_id,
        authoritative_charge=0.0,
    )

    def fail(stage):
        if stage == "after_reconciliation_update":
            raise RuntimeError("reconciliation crash")

    with pytest.raises(RuntimeError, match="reconciliation crash"):
        repository.resolve(
            reservation.attempt_id,
            actual_charge=0.0,
            outcome="not_charged",
            source="provider",
            actor_identity="provider:brave",
            idempotency_key="reconcile-fault-resolution",
            provider_snapshot_id=snapshot.snapshot_id,
            fault_hook=fail,
        )

    assert repository.get_attempt(reservation.attempt_id).status == "uncertain"
    with repository.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SpendAuditRow)) == 2


def test_provider_resolution_requires_a_matching_authoritative_snapshot(tmp_path):
    repository = _repository(tmp_path)
    reservation = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=10.0,
        caller_identity="maya",
        caller_label="",
        idempotency_key="provider-resolution",
    )
    wrong_reservation = repository.reserve(
        provider=ProviderName.SERPER,
        conservative_charge=1.0,
        budget_limit=10.0,
        caller_identity="maya",
        caller_label="",
        idempotency_key="wrong-provider-resolution",
    )
    wrong_snapshot = repository.record_provider_snapshot(
        provider=ProviderName.SERPER,
        balance=99.0,
        observed_at=datetime.now(tz=None),
        actor_identity="provider:serper",
        idempotency_key="wrong-provider-snapshot",
        provider_reference="serper-event-1",
        related_attempt_id=wrong_reservation.attempt_id,
        authoritative_charge=0.5,
    )

    with pytest.raises(Exception, match="matching provider snapshot"):
        repository.resolve(
            reservation.attempt_id,
            actual_charge=0.5,
            outcome="charged",
            source="provider",
            actor_identity="provider:brave",
            idempotency_key="provider-resolution-attempt-1",
        )
    with pytest.raises(Exception, match="matching provider snapshot"):
        repository.resolve(
            reservation.attempt_id,
            actual_charge=0.5,
            outcome="charged",
            source="provider",
            actor_identity="provider:brave",
            idempotency_key="provider-resolution-attempt-2",
            provider_snapshot_id=wrong_snapshot.snapshot_id,
        )

    snapshot = repository.record_provider_snapshot(
        provider=ProviderName.BRAVE,
        balance=9.5,
        observed_at=datetime.now(tz=None),
        actor_identity="provider:brave",
        idempotency_key="matching-provider-snapshot",
        provider_reference="brave-event-2",
        related_attempt_id=reservation.attempt_id,
        authoritative_charge=0.5,
    )
    resolved = repository.resolve(
        reservation.attempt_id,
        actual_charge=0.5,
        outcome="charged",
        source="provider",
        actor_identity="provider:brave",
        idempotency_key="provider-resolution-attempt-3",
        provider_snapshot_id=snapshot.snapshot_id,
    )
    assert resolved.status == "resolved"
    assert resolved.resolution_source == "provider"


def test_concurrent_reservations_cannot_overspend_budget(tmp_path):
    from argus.persistence.provider_spend import BudgetExhaustedError, SpendAttempt

    repository = _repository(tmp_path)
    barrier = Barrier(2)

    def reserve(key):
        barrier.wait()
        try:
            return repository.reserve(
                provider=ProviderName.BRAVE,
                conservative_charge=1.0,
                budget_limit=1.0,
                caller_identity="maya",
                caller_label="",
                idempotency_key=key,
            )
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, ("concurrent-a", "concurrent-b")))

    assert sum(isinstance(value, SpendAttempt) for value in outcomes) == 1
    assert sum(isinstance(value, BudgetExhaustedError) for value in outcomes) == 1
    assert repository.provider_summary(ProviderName.BRAVE, budget_limit=1.0)[
        "uncertain_charge"
    ] == 1.0


def test_free_attempt_records_outcome_without_monetary_reservation(tmp_path):
    repository = _repository(tmp_path)

    attempt = repository.record_free_attempt(
        provider=ProviderName.DUCKDUCKGO,
        outcome="success",
        usage=1.0,
        caller_identity="maya",
        caller_label="diagnostic",
        idempotency_key="free-attempt",
    )

    assert attempt.is_paid is False
    assert attempt.status == "settled"
    assert attempt.reserved_charge == 0.0
    assert attempt.actual_charge == 0.0
    assert attempt.usage == 1.0


@pytest.mark.asyncio
async def test_executor_reserves_before_provider_and_settles_known_charge(tmp_path):
    from argus.broker.budgets import BudgetTracker
    from argus.broker.execution import ProviderExecutor
    from argus.broker.health import HealthTracker
    from argus.models import ProviderStatus

    repository = _repository(tmp_path)
    observed = []

    class Provider:
        name = ProviderName.BRAVE

        def is_available(self):
            return True

        def status(self):
            return ProviderStatus.ENABLED

        async def search(self, query):
            summary = repository.provider_summary(
                ProviderName.BRAVE, budget_limit=10.0
            )
            observed.append(summary["uncertain_charge"])
            return [], ProviderTrace(
                provider=self.name,
                status="success",
                credit_info={"cost_usd": 0.4},
            )

    budgets = BudgetTracker()
    budgets.set_budget(ProviderName.BRAVE, 10.0)
    executor = ProviderExecutor(
        providers={ProviderName.BRAVE: Provider()},
        health_tracker=HealthTracker(),
        budget_tracker=budgets,
        spend_repository=repository,
    )

    await executor.execute(
        SearchQuery(
            query="paid",
            caller="maya",
            metadata={"caller_label": "supplied-label", "attempt_scope": "run-a"},
        ),
        [ProviderName.BRAVE],
    )

    assert observed == [1.0]
    summary = repository.provider_summary(ProviderName.BRAVE, budget_limit=10.0)
    assert summary["argus_estimated_charge"] == 0.4
    assert summary["uncertain_charge"] == 0.0


@pytest.mark.asyncio
async def test_executor_leaves_uncertain_charge_when_provider_acceptance_crashes(
    tmp_path,
):
    from argus.broker.budgets import BudgetTracker
    from argus.broker.execution import ProviderExecutor
    from argus.broker.health import HealthTracker
    from argus.models import ProviderStatus

    repository = _repository(tmp_path)

    class Provider:
        name = ProviderName.BRAVE

        def is_available(self):
            return True

        def status(self):
            return ProviderStatus.ENABLED

        async def search(self, query):
            raise BaseException("simulated process death after provider acceptance")

    budgets = BudgetTracker()
    budgets.set_budget(ProviderName.BRAVE, 10.0)
    executor = ProviderExecutor(
        providers={ProviderName.BRAVE: Provider()},
        health_tracker=HealthTracker(),
        budget_tracker=budgets,
        spend_repository=repository,
    )

    with pytest.raises(BaseException, match="process death"):
        await executor.execute(
            SearchQuery(query="paid", caller="maya"),
            [ProviderName.BRAVE],
        )

    assert repository.provider_summary(ProviderName.BRAVE, budget_limit=10.0)[
        "uncertain_charge"
    ] == 1.0


@pytest.mark.asyncio
async def test_executor_leaves_timeout_outcome_uncertain(tmp_path):
    from argus.broker.budgets import BudgetTracker
    from argus.broker.execution import ProviderExecutor
    from argus.broker.health import HealthTracker
    from argus.models import ProviderStatus

    repository = _repository(tmp_path)

    class Provider:
        name = ProviderName.BRAVE

        def is_available(self):
            return True

        def status(self):
            return ProviderStatus.ENABLED

        async def search(self, query):
            raise TimeoutError("response lost after request write")

    budgets = BudgetTracker()
    budgets.set_budget(ProviderName.BRAVE, 10.0)
    executor = ProviderExecutor(
        providers={ProviderName.BRAVE: Provider()},
        health_tracker=HealthTracker(),
        budget_tracker=budgets,
        spend_repository=repository,
    )

    outcome = await executor.execute(
        SearchQuery(query="paid", caller="maya"),
        [ProviderName.BRAVE],
    )

    assert outcome.traces[0].status == "error"
    assert repository.provider_summary(ProviderName.BRAVE, budget_limit=10.0)[
        "uncertain_charge"
    ] == 1.0


def test_scoped_credential_identity_overrides_supplied_caller_label(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.models import SearchMode, SearchResponse

    monkeypatch.setenv(
        "ARGUS_CALLER_CREDENTIALS_JSON",
        '{"maya":{"token":"maya-secret"}}',
    )
    seen = {}

    class Broker:
        async def search(self, query, **kwargs):
            seen["identity"] = query.caller
            seen["label"] = query.metadata["caller_label"]
            return SearchResponse(
                query=query.query,
                mode=SearchMode.DISCOVERY,
                results=[],
                search_run_id="scoped-caller",
            )

        budget_tracker = type("Budget", (), {"close": lambda self: None})()
        _reachability = type("Reachability", (), {"probe_all": lambda *a, **k: None})()
        _providers = {}

    class Repository:
        def accept(self, query, response):
            return None

    client = TestClient(create_app(broker=Broker(), search_repository=Repository()))
    response = client.post(
        "/api/search",
        json={"query": "who am i", "caller": "pretend-admin"},
        headers={"Authorization": "Bearer maya-secret"},
    )

    assert response.status_code == 200
    assert seen == {"identity": "maya", "label": "pretend-admin"}


@pytest.mark.asyncio
async def test_remote_mcp_verifier_derives_identity_from_scoped_credential(
    monkeypatch,
):
    from argus.auth import AuthConfig
    from argus.mcp.server import StaticTokenVerifier

    monkeypatch.setenv(
        "ARGUS_CALLER_CREDENTIALS_JSON",
        '{"maya":{"token":"maya-secret"}}',
    )
    verifier = StaticTokenVerifier(AuthConfig.from_env())

    accepted = await verifier.verify_token("maya-secret")

    assert accepted.client_id == "maya"
    assert await verifier.verify_token("wrong") is None


def test_admin_spend_interfaces_are_authenticated_and_audited(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from argus.api.main import create_app

    monkeypatch.setenv("ARGUS_ADMIN_API_KEY", "admin-secret")
    monkeypatch.setenv(
        "ARGUS_PROVIDER_RECONCILIATION_KEYS_JSON",
        '{"brave":"brave-reconciliation-secret"}',
    )
    repository = _repository(tmp_path)
    reservation = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=2000.0,
        caller_identity="maya",
        caller_label="",
        idempotency_key="admin-api-attempt",
    )
    snapshot_reservation = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=2000.0,
        caller_identity="maya",
        caller_label="",
        idempotency_key="admin-api-snapshot-attempt",
    )
    broker = type(
        "Broker",
        (),
        {
            "budget_tracker": type(
                "Budget",
                (),
                {
                    "close": lambda self: None,
                    "get_budget_limit": lambda self, provider: 2000.0,
                },
            )(),
        },
    )()
    client = TestClient(
        create_app(broker=broker, spend_repository=repository)
    )

    unauthorized = client.get("/api/admin/provider-spend")
    uncertain = client.get(
        "/api/admin/provider-spend/attempts?status=uncertain",
        headers={
            "X-Admin-API-Key": "admin-secret",
            "X-Provider-Reconciliation-Key": "brave-reconciliation-secret",
        },
    )
    resolved = client.post(
        f"/api/admin/provider-spend/attempts/{reservation.attempt_id}/resolve",
        headers={"X-Admin-API-Key": "admin-secret"},
        json={
            "actual_charge": 0.0,
            "outcome": "confirmed_not_consumed",
            "source": "operator",
            "idempotency_key": "admin-resolution",
        },
    )
    snapshot = client.post(
        "/api/admin/provider-spend/brave/snapshots",
        headers={
            "X-Admin-API-Key": "admin-secret",
            "X-Provider-Reconciliation-Key": "brave-reconciliation-secret",
        },
        json={
            "balance": 1999.0,
            "observed_at": datetime.now(tz=None).isoformat(),
            "provider_reference": "brave-admin-event",
            "related_attempt_id": snapshot_reservation.attempt_id,
            "authoritative_charge": 1.0,
            "idempotency_key": "admin-snapshot",
        },
    )
    summary = client.get(
        "/api/admin/provider-spend",
        headers={"X-Admin-API-Key": "admin-secret"},
    )

    assert unauthorized.status_code == 401
    assert uncertain.status_code == 200
    assert reservation.attempt_id in {
        attempt["attempt_id"] for attempt in uncertain.json()["attempts"]
    }
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert snapshot.status_code == 200
    brave = next(row for row in summary.json()["providers"] if row["provider"] == "brave")
    assert brave["estimate_source"] == "argus"
    assert brave["provider_snapshot"]["source"] == "provider"


def test_alembic_migration_creates_provider_spend_schema(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    path = tmp_path / "migrated-spend.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")

    tables = set(inspect(create_engine(f"sqlite:///{path}")).get_table_names())
    assert {
        "provider_spend_attempts",
        "provider_balance_snapshots",
        "provider_spend_audit",
    } <= tables


def test_postgresql_spend_reservation_and_settlement(postgres_ledger_url):
    from alembic import command
    from alembic.config import Config

    from argus.persistence.provider_spend import create_provider_spend_repository

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_ledger_url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    repository = create_provider_spend_repository(
        postgres_ledger_url, create_schema=False
    )

    reservation = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=1.0,
        budget_limit=2.0,
        caller_identity="maya",
        caller_label="",
        idempotency_key="postgres-paid",
    )
    repository.settle(
        reservation.attempt_id,
        actual_charge=0.5,
        outcome="success",
    )

    assert repository.provider_summary(ProviderName.BRAVE, budget_limit=2.0)[
        "remaining"
    ] == 1.5


def test_postgresql_concurrent_reservations_cannot_overspend(postgres_ledger_url):
    from alembic import command
    from alembic.config import Config

    from argus.persistence.provider_spend import (
        BudgetExhaustedError,
        SpendAttempt,
        create_provider_spend_repository,
    )

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_ledger_url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    repository = create_provider_spend_repository(
        postgres_ledger_url, create_schema=False
    )
    barrier = Barrier(2)

    def reserve(key):
        barrier.wait()
        try:
            return repository.reserve(
                provider=ProviderName.BRAVE,
                conservative_charge=1.0,
                budget_limit=1.0,
                caller_identity="maya",
                caller_label="",
                idempotency_key=key,
            )
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, ("pg-concurrent-a", "pg-concurrent-b")))

    assert sum(isinstance(value, SpendAttempt) for value in outcomes) == 1
    assert sum(isinstance(value, BudgetExhaustedError) for value in outcomes) == 1
