from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from argus.broker.readiness import ExecutionAuthorization, ExecutionDecision, ReadinessScope
from argus.contracts import FailureCode
from argus.extraction.spend_gateway import (
    ExtractionOperationContext,
    ExtractionSpendGateway,
)
from argus.models import ProviderName


def _context(**overrides) -> ExtractionOperationContext:
    values = {
        "operation_id": "extract-op-1",
        "request_id": "request-1",
        "plan_id": "plan-1",
        "request_hash": "request-hash-1",
        "caller_identity": "caller-1",
        "release_identity": "release-1",
    }
    values.update(overrides)
    return ExtractionOperationContext(**values)


def _readiness(*, allowed: bool = True) -> MagicMock:
    readiness = MagicMock()
    readiness.execution_scope.return_value = ReadinessScope(
        egress="datacenter",
        request_class="discovery",
        account_fingerprint="registration-account",
    )
    readiness.authorize_execution.return_value = ExecutionAuthorization(
        allowed=allowed,
        decision=ExecutionDecision(
            "eligible" if allowed else "spend_blocked",
            "authorized" if allowed else "exhausted",
            ("readiness",),
        ),
        provider=ProviderName.BRAVE,
        scope=ReadinessScope(
            egress="datacenter",
            request_class="discovery",
            account_fingerprint="account-1",
        ),
        owner="extraction:extract-op-1",
        scope_key="scope-1",
        fencing_token=2,
        attempt_id="attempt-1",
    )
    readiness.repository = SimpleNamespace()
    return readiness


def test_free_provider_does_not_call_spend_authority():
    readiness = _readiness()
    gateway = ExtractionSpendGateway(readiness_service=readiness, monotonic=lambda: 10)

    reservation = gateway.reserve(
        _context(free_only=True),
        ProviderName.DUCKDUCKGO,
        "not-applicable-account",
        estimate=None,
        deadline=None,
    )

    assert reservation.allowed
    assert reservation.status == "free"
    readiness.authorize_execution.assert_not_called()


def test_paid_reservation_propagates_operation_scope_and_release_identity():
    readiness = _readiness()
    gateway = ExtractionSpendGateway(readiness_service=readiness, monotonic=lambda: 10)

    reservation = gateway.reserve(
        _context(),
        ProviderName.BRAVE,
        "account-1",
        estimate=0.25,
        deadline=20,
    )

    assert reservation.allowed
    assert reservation.attempt_id == "attempt-1"
    execution = readiness.authorize_execution.call_args.args[0]
    assert execution.operation_id == "extract-op-1"
    assert execution.request_hash == "request-hash-1"
    assert execution.release_identity == "release-1"
    assert execution.scope.account_fingerprint == "account-1"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"estimate": None, "deadline": 20},
        {"estimate": 0, "deadline": 20},
        {"estimate": 1, "deadline": None},
        {"estimate": 1, "deadline": 10},
    ],
)
def test_paid_reservation_rejects_unknown_or_expired_contract(kwargs):
    readiness = _readiness()
    gateway = ExtractionSpendGateway(readiness_service=readiness, monotonic=lambda: 10)

    reservation = gateway.reserve(
        _context(), ProviderName.BRAVE, "account-1", **kwargs
    )

    assert not reservation.allowed
    assert reservation.failure is not None
    assert reservation.failure.code is FailureCode.SPEND_DENIED
    readiness.authorize_execution.assert_not_called()


def test_free_only_policy_rejects_paid_provider_before_authorization():
    readiness = _readiness()
    gateway = ExtractionSpendGateway(readiness_service=readiness)

    reservation = gateway.reserve(
        _context(free_only=True),
        ProviderName.BRAVE,
        "account-1",
        estimate=1,
        deadline=60,
    )

    assert not reservation.allowed
    assert reservation.failure.code is FailureCode.SPEND_DENIED
    readiness.authorize_execution.assert_not_called()


def test_denied_readiness_maps_to_typed_provider_failure():
    readiness = _readiness(allowed=False)
    gateway = ExtractionSpendGateway(readiness_service=readiness)

    reservation = gateway.reserve(
        _context(), ProviderName.BRAVE, "account-1", estimate=1, deadline=60
    )

    assert reservation.failure is not None
    assert reservation.failure.code is FailureCode.SPEND_DENIED


def test_settlement_requires_matching_request_hash_and_marks_unknown_charge():
    readiness = _readiness()
    gateway = ExtractionSpendGateway(readiness_service=readiness, monotonic=lambda: 10)
    reservation = gateway.reserve(
        _context(), ProviderName.BRAVE, "account-1", estimate=1, deadline=60
    )

    with pytest.raises(ValueError, match="operation hash"):
        gateway.settle(reservation, "success", 1.0, "provider-ref", "different")

    uncertain = gateway.settle(
        reservation, "success", None, "provider-ref", "request-hash-1"
    )
    assert uncertain.status == "uncertain"
    assert uncertain.failure.code is FailureCode.CHARGE_UNCERTAIN
    readiness.complete_execution.assert_called_once()
    assert readiness.complete_execution.call_args.kwargs["charge_known"] is False
