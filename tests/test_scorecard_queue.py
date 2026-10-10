"""Admission, backoff, and cleanup behavior for scorecard workloads."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from argus.scorecard.queue import (
    AdmissionLease,
    QueueClosed,
    QueueFull,
    QueueStatus,
    RetryPolicy,
    ScorecardExecutionQueue,
)


@pytest.mark.asyncio
async def test_second_workload_waits_without_starting_until_first_releases() -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    async def first() -> str:
        first_started.set()
        await release_first.wait()
        return "first"

    async def second() -> str:
        second_started.set()
        return "second"

    queue = ScorecardExecutionQueue[str](max_active=1, max_queued=2)
    first_job = queue.submit("baseline", first)
    second_job = queue.submit("candidate", second)

    await asyncio.wait_for(first_started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert second_job.status.status is QueueStatus.WAITING
    assert not second_started.is_set()

    release_first.set()
    assert await first_job.wait() == "first"
    assert await second_job.wait() == "second"
    assert second_started.is_set()
    assert first_job.status.status is QueueStatus.COMPLETED
    assert second_job.status.status is QueueStatus.COMPLETED
    await queue.aclose()


@pytest.mark.asyncio
async def test_capacity_denial_uses_bounded_backoff_and_starts_work_once() -> None:
    attempts: list[float] = []
    admit_count = 0

    async def admit(_work_id: str) -> AdmissionLease | None:
        nonlocal admit_count
        attempts.append(asyncio.get_running_loop().time())
        admit_count += 1
        if admit_count < 3:
            return None
        return AdmissionLease()

    queue = ScorecardExecutionQueue[str](
        retry_policy=RetryPolicy(
            max_attempts=4,
            base_delay=0.005,
            max_delay=0.01,
            max_wait=0.1,
        ),
        admit=admit,
    )
    started = 0

    async def work() -> str:
        nonlocal started
        started += 1
        return "ok"

    job = queue.submit("candidate", work)
    assert await job.wait() == "ok"
    assert started == 1
    assert len(attempts) == 3
    assert attempts[1] - attempts[0] >= 0.004
    assert attempts[2] - attempts[1] >= 0.004
    await queue.aclose()


@pytest.mark.asyncio
async def test_cancellation_releases_waiting_work_and_startup_failure_does_too() -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    async def first() -> None:
        first_started.set()
        await release_first.wait()

    async def second() -> str:
        second_started.set()
        return "second"

    queue = ScorecardExecutionQueue[str](max_active=1, max_queued=3)
    first_job = queue.submit("first", first)
    waiting_job = queue.submit("waiting", second)
    await asyncio.wait_for(first_started.wait(), timeout=1)
    await waiting_job.cancel()
    assert waiting_job.status.status is QueueStatus.CANCELED
    assert not second_started.is_set()

    release_first.set()
    await first_job.wait()

    async def fails() -> None:
        raise RuntimeError("startup failed")

    failed_job = queue.submit("failing", fails)
    successor = queue.submit("successor", second)
    with pytest.raises(RuntimeError, match="startup failed"):
        await failed_job.wait()
    assert await successor.wait() == "second"
    assert failed_job.status.status is QueueStatus.FAILED
    assert successor.status.status is QueueStatus.COMPLETED
    await queue.aclose()


@pytest.mark.asyncio
async def test_canceling_admitted_work_releases_lease_before_return() -> None:
    started = asyncio.Event()
    release_calls = 0

    async def release() -> None:
        nonlocal release_calls
        release_calls += 1

    async def blocked() -> None:
        started.set()
        await asyncio.Event().wait()

    queue = ScorecardExecutionQueue[None](
        max_active=1,
        admit=lambda _work_id: AdmissionLease(release),
    )
    job = queue.submit("running", blocked)
    await asyncio.wait_for(started.wait(), timeout=1)

    await job.cancel()

    assert job.status.status is QueueStatus.CANCELED
    assert release_calls == 1
    await queue.aclose()


@pytest.mark.asyncio
async def test_expired_admission_is_released_and_retried() -> None:
    released = 0
    calls = 0

    async def release() -> None:
        nonlocal released
        released += 1

    async def admit(_work_id: str) -> AdmissionLease:
        nonlocal calls
        calls += 1
        if calls == 1:
            return AdmissionLease(release, expires_at=asyncio.get_running_loop().time() - 1)
        return AdmissionLease(release)

    queue = ScorecardExecutionQueue[str](
        retry_policy=RetryPolicy(
            max_attempts=3,
            base_delay=0.001,
            max_delay=0.001,
            max_wait=0.1,
        ),
        admit=admit,
    )
    job = queue.submit("expired", lambda: "ok")
    assert await job.wait() == "ok"
    assert calls == 2
    assert released == 2
    await queue.aclose()


@pytest.mark.asyncio
async def test_cancel_before_first_scheduling_turn_removes_waiting_work() -> None:
    started = False

    def work() -> str:
        nonlocal started
        started = True
        return "unexpected"

    queue = ScorecardExecutionQueue[str]()
    job = queue.submit("never-started", work)
    await job.cancel()

    assert not started
    assert job.status.status is QueueStatus.CANCELED
    await queue.aclose()


@pytest.mark.asyncio
async def test_status_distinguishes_lifecycle_states_without_host_details() -> None:
    gate = asyncio.Event()
    started = asyncio.Event()

    async def blocking() -> None:
        started.set()
        await gate.wait()

    async def failing() -> None:
        raise RuntimeError("workload failed")

    queue = ScorecardExecutionQueue[None](max_active=1, max_queued=4)
    running = queue.submit("running", blocking)
    await asyncio.wait_for(started.wait(), timeout=1)
    assert running.status.status is QueueStatus.ADMITTED

    waiting = queue.submit("waiting", blocking)
    assert waiting.status.status is QueueStatus.WAITING
    await waiting.cancel()
    assert waiting.status.status is QueueStatus.CANCELED

    gate.set()
    await running.wait()
    assert running.status.status is QueueStatus.COMPLETED

    failed = queue.submit("failing", failing)
    with pytest.raises(RuntimeError, match="workload failed"):
        await failed.wait()
    assert failed.status.status is QueueStatus.FAILED

    observed = {job.status.status for job in (running, waiting, failed)}
    assert observed == {
        QueueStatus.COMPLETED,
        QueueStatus.CANCELED,
        QueueStatus.FAILED,
    }
    for job in (running, waiting, failed):
        assert {field.name for field in dataclasses.fields(job.status)} == {
            "work_id",
            "status",
            "attempts",
            "failure_code",
            "retry_after_seconds",
        }
        host_fields = ("lease", "expires_at", "capacity", "host", "machine", "egress")
        for host_detail in host_fields:
            assert not hasattr(job.status, host_detail)
    await queue.aclose()


@pytest.mark.asyncio
async def test_bounded_queue_rejects_work_beyond_capacity_and_after_close() -> None:
    gate = asyncio.Event()

    async def blocking() -> None:
        await gate.wait()

    queue = ScorecardExecutionQueue[None](max_active=1, max_queued=1)
    first = queue.submit("first", blocking)
    with pytest.raises(QueueFull):
        queue.submit("second", blocking)

    gate.set()
    await first.wait()
    await queue.aclose()
    with pytest.raises(QueueClosed):
        queue.submit("third", blocking)
