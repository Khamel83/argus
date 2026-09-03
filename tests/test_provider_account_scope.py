"""Account-scoped provider spend isolation contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from argus.models import ProviderName


def _repository(tmp_path):
    from argus.persistence.provider_spend import create_provider_spend_repository

    return create_provider_spend_repository(
        f"sqlite:///{tmp_path / 'account-scoped.db'}",
        create_schema=True,
    )


def test_budget_obligations_are_isolated_per_provider_account(tmp_path):
    repository = _repository(tmp_path)

    first = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=0.75,
        budget_limit=1.0,
        caller_identity="caller-a",
        caller_label="account-a",
        account_fingerprint="account-a",
        idempotency_key="account-a:one",
    )
    second = repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=0.75,
        budget_limit=1.0,
        caller_identity="caller-b",
        caller_label="account-b",
        account_fingerprint="account-b",
        idempotency_key="account-b:one",
    )

    assert first.account_fingerprint == "account-a"
    assert second.account_fingerprint == "account-b"

    with pytest.raises(Exception, match="budget exhausted"):
        repository.reserve(
            provider=ProviderName.BRAVE,
            conservative_charge=0.3,
            budget_limit=1.0,
            caller_identity="caller-a",
            caller_label="account-a",
            account_fingerprint="account-a",
            idempotency_key="account-a:two",
        )


def test_reservation_identity_includes_execution_deadline(tmp_path):
    from argus.persistence.provider_spend import SpendConflictError

    repository = _repository(tmp_path)
    base = datetime.now(timezone.utc)
    repository.reserve(
        provider=ProviderName.BRAVE,
        conservative_charge=0.25,
        budget_limit=10.0,
        caller_identity="caller",
        caller_label="deadline",
        account_fingerprint="account",
        operation_id="operation",
        release_identity="release",
        execution_deadline=base,
        idempotency_key="same-deadline-key",
    )

    with pytest.raises(SpendConflictError):
        repository.reserve(
            provider=ProviderName.BRAVE,
            conservative_charge=0.25,
            budget_limit=10.0,
            caller_identity="caller",
            caller_label="deadline",
            account_fingerprint="account",
            operation_id="operation",
            release_identity="release",
            execution_deadline=base + timedelta(seconds=1),
            idempotency_key="same-deadline-key",
        )
