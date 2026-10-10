"""Queued scorecard workload startup guarded by capacity admission."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable, Generic, TypeVar

from .admission import (
    AdmissionDecision,
    CapacityProbe,
    ScorecardAdmissionPolicy,
)


WorkloadT = TypeVar("WorkloadT")


@dataclass(slots=True)
class AdmittedWorkload(Generic[WorkloadT]):
    """A created workload and its release handle.

    A denied result has ``workload is None`` and no release side effect.  The
    release method is idempotent so completion paths can safely use ``finally``.
    """

    workload: WorkloadT | None
    decision: AdmissionDecision
    _release_callback: Callable[[], None] | None = None

    @property
    def admitted(self) -> bool:
        return self.decision.admitted

    def release(self) -> None:
        callback, self._release_callback = self._release_callback, None
        if callback is not None:
            callback()


class ScorecardCoordinator:
    """Serialize admission and Docker workload creation for scorecard jobs.

    A capacity sample is taken before a wait, after every wait interval, and
    while the creation lock is held immediately before ``create_workload``.
    The factory is never called after a rejected decision.
    """

    def __init__(
        self,
        policy: ScorecardAdmissionPolicy,
        probe: CapacityProbe,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._policy = policy
        self._probe = probe
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._active_jobs = 0

    @property
    def active_jobs(self) -> int:
        with self._lock:
            return self._active_jobs

    def _check(self) -> AdmissionDecision:
        snapshot = self._probe.sample()
        return self._policy.evaluate(snapshot.with_active_jobs(self.active_jobs))

    def start_workload(
        self,
        create_workload: Callable[[], WorkloadT],
        *,
        wait_timeout_seconds: float = 0.0,
        wait_interval_seconds: float = 5.0,
    ) -> AdmittedWorkload[WorkloadT]:
        """Wait for bounded capacity, then create exactly one workload.

        Waiting is optional and bounded.  A zero timeout performs the initial
        and immediate pre-creation checks without sleeping.
        """
        if wait_timeout_seconds < 0 or wait_interval_seconds <= 0:
            raise ValueError("wait timeout must be non-negative and interval positive")

        deadline = self._clock() + wait_timeout_seconds
        decision = self._check()
        while not decision.admitted and self._clock() < deadline:
            remaining = deadline - self._clock()
            self._sleeper(min(wait_interval_seconds, max(0.0, remaining)))
            decision = self._check()

        if not decision.admitted:
            return AdmittedWorkload(None, decision)

        # This lock makes the last probe and reservation one admission seam;
        # another coordinator call cannot consume the same active-job budget.
        with self._lock:
            decision = self._policy.evaluate(
                self._probe.sample().with_active_jobs(self._active_jobs)
            )
            if not decision.admitted:
                return AdmittedWorkload(None, decision)
            self._active_jobs += 1
            try:
                workload = create_workload()
            except BaseException:
                self._active_jobs -= 1
                raise

        return AdmittedWorkload(workload, decision, self._release)

    # Short alias for callers treating the coordinator as the admission seam.
    start = start_workload

    def _release(self) -> None:
        with self._lock:
            if self._active_jobs > 0:
                self._active_jobs -= 1


__all__ = ["AdmittedWorkload", "ScorecardCoordinator"]
