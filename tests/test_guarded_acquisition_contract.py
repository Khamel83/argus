"""Transport-neutral contract tests for guarded acquisition.

The contract deliberately has no provider, DNS, socket, or browser work.  It
only describes the bounded values that the later acquisition implementations
may exchange.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest

from argus.acquisition import (
    AcquisitionFailure,
    AcquisitionLimits,
    AcquisitionRequest,
    AcquisitionResult,
    GuardedAcquisition,
    GuardedBrowserSession,
    OriginProfile,
)


def _request(**overrides) -> AcquisitionRequest:
    values = {
        "normalized_url": "https://public.example/article",
        "operation_class": "direct_http",
        "profile": OriginProfile.PUBLIC_CONTENT,
        "credential_policy": "none",
        "limits": AcquisitionLimits(),
        "caller_principal": "contract-test",
        "request_id": "request-1",
    }
    values.update(overrides)
    return AcquisitionRequest(**values)


def test_origin_profiles_are_closed_and_explicit():
    assert tuple(profile.value for profile in OriginProfile) == (
        "public_content",
        "authenticated_content",
        "third_party_fetch",
    )
    assert OriginProfile("public_content") is OriginProfile.PUBLIC_CONTENT
    with pytest.raises(ValueError):
        OriginProfile("implicit_credentials")


def test_contract_values_are_frozen_dataclasses():
    request = _request()
    limits = request.limits

    assert is_dataclass(request)
    assert is_dataclass(limits)
    with pytest.raises(FrozenInstanceError):
        request.request_id = "changed"
    with pytest.raises(FrozenInstanceError):
        limits.max_body_bytes = 1


def test_request_fields_are_bounded_before_dispatch():
    with pytest.raises(ValueError):
        _request(normalized_url="https://example.test/" + "x" * 2048)
    with pytest.raises(ValueError):
        _request(operation_class="o" * 65)
    with pytest.raises(ValueError):
        _request(caller_principal="p" * 129)
    with pytest.raises(ValueError):
        _request(request_id="r" * 65)
    with pytest.raises(ValueError):
        _request(limits=AcquisitionLimits(max_body_bytes=0))
    with pytest.raises(ValueError):
        _request(limits=AcquisitionLimits(max_redirect_hops=101))


def test_failure_constructors_keep_stable_safe_categories_and_bounds():
    browser_failure = AcquisitionFailure.browser_policy_unavailable(
        request_id="r1"
    )
    blocked_failure = AcquisitionFailure.acquisition_blocked(
        request_id="r2",
        reason="approved address set was not available",
        evidence_references=("dns-check-1",),
    )

    assert browser_failure.code == "browser_policy_unavailable"
    assert browser_failure.retryable is False
    assert browser_failure.before_browser_creation is True
    assert browser_failure.request_id == "r1"
    assert blocked_failure.code == "acquisition_blocked"
    assert blocked_failure.retryable is False
    assert blocked_failure.safe_reason == (
        "approved address set was not available"
    )
    assert blocked_failure.evidence_references == ("dns-check-1",)
    assert len(blocked_failure.safe_reason) <= 256

    with pytest.raises(ValueError):
        AcquisitionFailure(
            code="acquisition_blocked",
            safe_reason="x" * 257,
            retryable=False,
            request_id="r1",
        )
    with pytest.raises(ValueError):
        AcquisitionFailure(
            code="acquisition_blocked",
            safe_reason="safe",
            retryable=False,
            request_id="r1",
            evidence_references=("evidence",) * 17,
        )


def test_result_and_browser_session_expose_only_bounded_contract_values():
    result_names = {item.name for item in fields(AcquisitionResult)}
    session_names = {item.name for item in fields(GuardedBrowserSession)}
    forbidden = {"client", "socket", "page", "browser", "context"}
    assert result_names.isdisjoint(forbidden)
    assert session_names.isdisjoint(forbidden)
    assert is_dataclass(AcquisitionResult)
    assert is_dataclass(GuardedBrowserSession)

    for name in result_names | session_names:
        assert name not in forbidden


def test_guarded_acquisition_is_transport_neutral_and_has_no_raw_handle_api():
    assert hasattr(GuardedAcquisition, "acquire")
    assert hasattr(GuardedAcquisition, "open_browser_session")
    names = set(dir(GuardedAcquisition))
    assert names.isdisjoint({"client", "socket", "page", "browser", "context"})
