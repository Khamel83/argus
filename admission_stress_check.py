"""Throwaway concurrency stress for the scorecard admission coordinator."""

from __future__ import annotations

import multiprocessing as mp
import sys
import tempfile
import threading
from pathlib import Path

from argus.scorecard.admission import (
    AdmissionState,
    ScorecardAdmissionCoordinator,
)

ROOT = Path(tempfile.mkdtemp(prefix="admission-stress-"))


def _worker(state_path: str, limit: int, rounds: int, failures: list, index: int):
    coordinator = ScorecardAdmissionCoordinator(
        max_active_workloads=limit, state_path=state_path
    )
    counter = mp.Value("i", 0)
    mode = "baseline" if index % 2 == 0 else "candidate"
    for _ in range(rounds):
        result = coordinator.acquire(mode)
        if result.state is not AdmissionState.ADMITTED:
            if result.state is not AdmissionState.QUEUED:
                failures.append(f"unexpected state {result.state.value}")
            continue
        with counter.get_lock():
            counter.value += 1
            if counter.value > limit:
                failures.append(
                    f"over-admission: {counter.value} active with limit {limit}"
                )
        threading.Event().wait(0.005)
        with counter.get_lock():
            counter.value -= 1
        result.lease.release()


def _threads(state_path: str, limit: int, rounds: int, failures: list):
    coordinator = ScorecardAdmissionCoordinator(
        max_active_workloads=limit, state_path=state_path
    )
    counter = {"value": 0, "lock": threading.Lock()}
    barrier = threading.Barrier(8)

    def one(index: int):
        barrier.wait()
        mode = "baseline" if index % 2 == 0 else "candidate"
        for _ in range(rounds):
            result = coordinator.acquire(mode)
            if result.state is not AdmissionState.ADMITTED:
                continue
            with counter["lock"]:
                counter["value"] += 1
                if counter["value"] > limit:
                    failures.append(
                        f"thread over-admission: {counter['value']} > {limit}"
                    )
            threading.Event().wait(0.002)
            with counter["lock"]:
                counter["value"] -= 1
            result.lease.release()

    threads = [threading.Thread(target=one, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def _run(label: str, limit: int, workers: int, rounds: int):
    state_path = str(ROOT / f"{label}.state")
    failures: list = []
    with mp.Manager() as manager:
        shared = manager.list()
        processes = [
            mp.Process(
                target=_worker,
                args=(state_path, limit, rounds, shared, index),
            )
            for index in range(workers)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(60)
        failures.extend(shared)
    _threads(state_path, limit, rounds, failures)
    # A lease must not survive the whole stress round.
    final = ScorecardAdmissionCoordinator(
        max_active_workloads=limit, state_path=state_path
    ).acquire("candidate")
    if final.state is not AdmissionState.ADMITTED:
        failures.append(f"residual lease after stress: {final.state.value}")
    else:
        final.lease.release()
    print(f"{label}: limit={limit} workers={workers} failures={len(failures)}")
    for failure in failures[:5]:
        print(f"  {failure}")
    return not failures


if __name__ == "__main__":
    ok = _run("processes-limit-1", 1, 8, 20)
    ok = _run("processes-limit-3", 3, 12, 15) and ok
    print("RESULT", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
