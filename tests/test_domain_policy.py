from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest


def _repository(tmp_path, **kwargs):
    from argus.persistence.domain_policy import create_domain_policy_repository

    return create_domain_policy_repository(
        f"sqlite:///{tmp_path / 'domain-policy.db'}",
        **kwargs,
    )


def test_normalize_domain_accepts_hostnames_and_safely_rejects_invalid_values():
    from argus.persistence.domain_policy import normalize_domain

    assert normalize_domain("  Example.COM. ") == "example.com"
    assert normalize_domain("https://EXAMPLE.com/article") == "example.com"
    assert normalize_domain("") is None
    assert normalize_domain(None) is None
    assert normalize_domain("localhost") is None
    assert normalize_domain("not a domain") is None
    assert normalize_domain("https://user:pass@example.com") is None


def test_policy_value_is_immutable_and_survives_repository_session_close(tmp_path):
    repository = _repository(tmp_path)

    value = repository.record_datacenter_failure(
        "Example.COM.",
        reason="blocked",
        event_identity="failure-1",
    )

    assert value.domain == "example.com"
    assert value.datacenter_failure_count == 1
    assert value.residential_success_count == 0
    assert value.prefer_residential_search is False
    assert value.prefer_residential_extraction is False
    assert value.failure_reason == "blocked"
    assert value.version == 1
    assert value.updated_at.tzinfo == timezone.utc
    assert repository.get_policy("EXAMPLE.com") == value
    with pytest.raises(FrozenInstanceError):
        value.domain = "other.example"  # type: ignore[misc]


def test_first_success_initializes_all_policy_fields_and_preference(tmp_path):
    repository = _repository(tmp_path)

    value = repository.record_residential_success(
        "example.com",
        event_identity="success-1",
    )

    assert value.domain == "example.com"
    assert value.prefer_residential_search is True
    assert value.prefer_residential_extraction is True
    assert value.datacenter_failure_count == 0
    assert value.residential_success_count == 1
    assert value.last_datacenter_failure is None
    assert value.last_residential_success is not None
    assert value.failure_reason is None
    assert value.updated_at.tzinfo == timezone.utc
    assert value.version == 1


def test_failure_preference_starts_at_the_third_datacenter_failure(tmp_path):
    repository = _repository(tmp_path)

    first = repository.record_datacenter_failure(
        "example.com", event_identity="failure-1"
    )
    second = repository.record_datacenter_failure(
        "example.com", event_identity="failure-2"
    )
    third = repository.record_datacenter_failure(
        "example.com", event_identity="failure-3"
    )

    assert first.prefer_residential_extraction is False
    assert second.prefer_residential_search is False
    assert third.prefer_residential_extraction is True
    assert third.prefer_residential_search is True
    assert third.datacenter_failure_count == 3
    assert third.version == 3


def test_duplicate_event_replays_original_value_without_incrementing(tmp_path):
    repository = _repository(tmp_path)

    first = repository.record_datacenter_failure(
        "example.com",
        reason="blocked",
        event_identity="same-event",
    )
    replay = repository.record_datacenter_failure(
        "example.com",
        reason="blocked",
        event_identity="same-event",
    )

    assert replay == first
    assert repository.get_policy("example.com") == first
    assert repository.count_events("same-event") == 1


def test_reusing_event_identity_with_different_request_fails_without_mutation(tmp_path):
    from argus.persistence.domain_policy import DomainPolicyConflictError

    repository = _repository(tmp_path)
    first = repository.record_datacenter_failure(
        "example.com",
        reason="first",
        event_identity="same-event",
    )

    with pytest.raises(DomainPolicyConflictError):
        repository.record_datacenter_failure(
            "example.com",
            reason="different",
            event_identity="same-event",
        )

    assert repository.get_policy("example.com") == first
    assert repository.count_events("same-event") == 1


def test_fault_after_upsert_rolls_back_policy_and_event(tmp_path):
    repository = _repository(tmp_path)

    def fail(stage: str) -> None:
        if stage == "after_policy_upsert":
            raise RuntimeError("injected database fault")

    with pytest.raises(RuntimeError, match="injected database fault"):
        repository.record_datacenter_failure(
            "example.com",
            event_identity="faulted-event",
            fault_hook=fail,
        )

    assert repository.get_policy("example.com") is None
    assert repository.count_events("faulted-event") == 0


def test_domain_memory_facade_returns_values_and_uses_safe_invalid_behavior(tmp_path):
    from argus.extraction.domain_memory import DomainMemory

    facade = DomainMemory(repository=_repository(tmp_path))

    assert facade.get_policy("not a domain") is None
    assert facade.should_prefer_residential("not a domain") is False
    assert facade.record_datacenter_failure("not a domain") is None
    assert facade.record_residential_success("") is None
    value = facade.record_residential_success(
        "Example.com.", event_identity="facade-success"
    )
    assert value is not None
    assert facade.get_policy("example.com") == value
    assert facade.should_prefer_residential("example.com", "search") is True


def test_injected_naive_clock_is_normalized_to_utc(tmp_path):
    repository = _repository(
        tmp_path,
        clock=lambda: datetime(2026, 9, 2, 12, 0, 0),
    )

    value = repository.record_datacenter_failure(
        "example.com", event_identity="naive-clock"
    )

    assert value.updated_at == datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
    assert value.last_datacenter_failure == value.updated_at


def test_known_deadlock_retries_the_complete_command_with_a_bound(tmp_path):
    from sqlalchemy.exc import OperationalError
    from argus.persistence.domain_policy import DomainPolicyValue

    repository = _repository(tmp_path, max_retries=2, retry_delay_seconds=0)
    expected = DomainPolicyValue(
        domain="example.com",
        prefer_residential_search=False,
        prefer_residential_extraction=False,
        datacenter_failure_count=1,
        residential_success_count=0,
        last_datacenter_failure=datetime.now(timezone.utc),
        last_residential_success=None,
        failure_reason=None,
        updated_at=datetime.now(timezone.utc),
        version=1,
    )
    calls = 0

    def flaky(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OperationalError(
                "deadlock detected",
                {},
                RuntimeError("deadlock detected"),
            )
        return expected

    repository._record_once = flaky  # type: ignore[method-assign]

    assert (
        repository.record_datacenter_failure(
            "example.com", event_identity="bounded-retry"
        )
        == expected
    )
    assert calls == 3


def test_ambiguous_commit_reconciles_event_before_returning(tmp_path):
    from sqlalchemy.exc import OperationalError

    repository = _repository(tmp_path, max_retries=0, retry_delay_seconds=0)
    expected = repository.record_residential_success(
        "example.com", event_identity="ambiguous-reconciled"
    )
    error = OperationalError("connection reset", {}, RuntimeError("connection reset"))
    error.connection_invalidated = True

    def ambiguous(*args, **kwargs):
        raise error

    repository._record_once = ambiguous  # type: ignore[method-assign]
    repository._reconcile_event = lambda identity, request_hash: expected  # type: ignore[method-assign]

    assert (
        repository.record_residential_success(
            "example.com", event_identity="ambiguous-reconciled"
        )
        == expected
    )
