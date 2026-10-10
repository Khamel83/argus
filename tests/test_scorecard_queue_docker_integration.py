"""Integration test: scorecard baseline/candidate execution without early Docker init."""

from __future__ import annotations

import asyncio

import pytest

from argus.scorecard.queue import (
    AdmissionLease,
    CapacityDenied,
    QueueStatus,
    RetryPolicy,
    ScorecardExecutionQueue,
)


@pytest.mark.asyncio
async def test_baseline_candidate_no_docker_init_before_admission() -> None:
    """Verify Docker initialization doesn't happen until admission succeeds.

    This test simulates a realistic scorecard execution where:
    - Docker resource initialization is mocked as part of the work callback
    - Admission control checks capacity constraints
    - Waiting jobs do NOT initialize Docker resources
    - Only admitted jobs initialize Docker resources
    """
    docker_inits: list[str] = []
    admission_calls: list[str] = []
    active_slots = 0
    max_slots = 1

    async def mock_admission(work_id: str) -> AdmissionLease | None:
        """Mock admission control that limits concurrent execution."""
        nonlocal active_slots
        admission_calls.append(work_id)
        if active_slots >= max_slots:
            raise CapacityDenied(f"capacity denied for {work_id}")
        active_slots += 1

        async def release() -> None:
            nonlocal active_slots
            active_slots -= 1

        return AdmissionLease(release)

    baseline_started = asyncio.Event()
    baseline_released = asyncio.Event()

    async def baseline_work() -> str:
        """Baseline job: initializes Docker and runs evaluation."""
        docker_inits.append("baseline")
        baseline_started.set()
        await baseline_released.wait()
        return "baseline-result"

    async def candidate_work() -> str:
        """Candidate job: initializes Docker and runs evaluation."""
        docker_inits.append("candidate")
        await asyncio.sleep(0.01)  # Simulate work
        return "candidate-result"

    # Create queue with max 1 active job
    queue = ScorecardExecutionQueue[str](
        max_active=1,
        max_queued=3,
        retry_policy=RetryPolicy(max_attempts=10, base_delay=0.001, max_wait=1.0),
        admit=mock_admission,
    )

    # Submit baseline and candidate jobs
    baseline_job = queue.submit("baseline", baseline_work)
    candidate_job = queue.submit("candidate", candidate_work)

    # Wait until the baseline holds the only admission slot
    await asyncio.wait_for(baseline_started.wait(), timeout=1)

    # Candidate should be waiting; Docker not initialized for candidate yet
    assert candidate_job.status.status is QueueStatus.WAITING
    assert docker_inits == ["baseline"], "Docker should only be init'd for admitted baseline"
    assert len(admission_calls) >= 1, "Admission should have been called at least once"

    # Release the baseline and wait for it to complete
    baseline_released.set()
    result = await baseline_job.wait()
    assert result == "baseline-result"
    assert baseline_job.status.status is QueueStatus.COMPLETED

    # Now candidate should be admitted and Docker initialized
    result = await candidate_job.wait()
    assert result == "candidate-result"
    assert candidate_job.status.status is QueueStatus.COMPLETED

    # Both Docker inits should have occurred, but never concurrently
    assert set(docker_inits) == {"baseline", "candidate"}
    assert docker_inits == ["baseline", "candidate"], "Docker init order must be preserved"

    await queue.aclose()


@pytest.mark.asyncio
async def test_capacity_denial_retries_with_bounded_backoff_no_resource_leak() -> None:
    """Verify capacity denial doesn't leak resources during retries.

    Resources (Docker initialization) should only happen after admission succeeds,
    not during retry backoff phases.
    """
    docker_inits: list[tuple[str, int]] = []  # (work_id, attempt)
    admission_attempts: list[int] = []
    max_concurrent = 1
    current_active = 0

    async def admission_with_denial(work_id: str) -> AdmissionLease | None:
        """Deny admission on first 2 attempts."""
        nonlocal current_active
        admission_attempts.append(len(admission_attempts))
        attempt_num = len(admission_attempts)

        if attempt_num <= 2:
            raise CapacityDenied(f"capacity denied for {work_id} on attempt {attempt_num}")

        if current_active >= max_concurrent:
            raise CapacityDenied(f"max concurrent reached for {work_id}")

        current_active += 1

        async def release() -> None:
            nonlocal current_active
            current_active -= 1

        return AdmissionLease(release)

    async def work_with_docker_init(work_id: str) -> str:
        """Work that initializes Docker (only after admission)."""
        docker_inits.append((work_id, len(admission_attempts)))
        await asyncio.sleep(0.001)
        return f"{work_id}-result"

    queue = ScorecardExecutionQueue[str](
        max_active=1,
        max_queued=1,
        retry_policy=RetryPolicy(
            max_attempts=5,
            base_delay=0.001,
            max_delay=0.01,
            max_wait=1.0,
        ),
        admit=admission_with_denial,
    )

    job = queue.submit("test-job", lambda: work_with_docker_init("test-job"))
    result = await job.wait()

    # Should have succeeded after 3 admission attempts (denied on 1st and 2nd)
    assert result == "test-job-result"
    assert len(admission_attempts) == 3

    # Docker should only be initialized after successful admission (attempt 3)
    assert len(docker_inits) == 1
    assert docker_inits[0] == ("test-job", 3)

    await queue.aclose()


@pytest.mark.asyncio
async def test_cancellation_prevents_docker_resource_initialization() -> None:
    """Verify cancellation stops work before Docker initialization.

    If a job is canceled while waiting or immediately after admission,
    Docker resources should never be initialized.
    """
    docker_inits: list[str] = []

    async def mock_admission(work_id: str) -> AdmissionLease | bool:
        """Admission always succeeds."""
        return True

    async def work_with_docker(work_id: str) -> str:
        """Simulate Docker initialization."""
        docker_inits.append(work_id)
        await asyncio.sleep(0.1)
        return "result"

    queue = ScorecardExecutionQueue[str](
        max_active=1,
        max_queued=3,
        admit=mock_admission,
    )

    # Submit three jobs
    job1 = queue.submit("job1", lambda: work_with_docker("job1"))
    job2 = queue.submit("job2", lambda: work_with_docker("job2"))
    job3 = queue.submit("job3", lambda: work_with_docker("job3"))

    # Let job1 start
    await asyncio.sleep(0.01)

    # Cancel waiting jobs
    await job2.cancel()
    await job3.cancel()

    # Only job1 should have started Docker
    assert job2.status.status is QueueStatus.CANCELED
    assert job3.status.status is QueueStatus.CANCELED

    # Let job1 complete
    result = await job1.wait()
    assert result == "result"

    # Only job1 should have initialized Docker
    assert docker_inits == ["job1"]

    await queue.aclose()
