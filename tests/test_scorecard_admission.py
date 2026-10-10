"""Host-wide scorecard admission contracts."""

from __future__ import annotations

from multiprocessing import Event, Process
from pathlib import Path

import pytest

from argus.config import ArgusConfig
from argus.scorecard.admission import (
    AdmissionState,
    ScorecardAdmissionCoordinator,
    configured_scorecard_active_workload_limit,
)


def test_default_limit_and_bounded_configuration(monkeypatch):
    monkeypatch.delenv("ARGUS_SCORECARD_ACTIVE_WORKLOAD_LIMIT", raising=False)
    assert configured_scorecard_active_workload_limit() == 1
    monkeypatch.setenv("ARGUS_SCORECARD_ACTIVE_WORKLOAD_LIMIT", "2")
    assert configured_scorecard_active_workload_limit() == 2
    monkeypatch.setenv("ARGUS_SCORECARD_ACTIVE_WORKLOAD_LIMIT", "0")
    with pytest.raises(ValueError):
        configured_scorecard_active_workload_limit()
    monkeypatch.setenv("ARGUS_SCORECARD_ACTIVE_WORKLOAD_LIMIT", "33")
    with pytest.raises(ValueError):
        configured_scorecard_active_workload_limit()


def test_config_surface_shares_the_bounded_limit_contract():
    assert ArgusConfig().scorecard_active_workload_limit == 1
    assert (
        ArgusConfig(scorecard_active_workload_limit=2).scorecard_active_workload_limit
        == 2
    )
    for rejected in (0, 33, True):
        with pytest.raises(ValueError):
            ArgusConfig(scorecard_active_workload_limit=rejected)


def test_baseline_and_candidate_share_one_capacity_before_startup(tmp_path: Path):
    coordinator = ScorecardAdmissionCoordinator(state_path=tmp_path / "admission")
    first = coordinator.acquire("baseline")
    assert first.state is AdmissionState.ADMITTED

    started: list[str] = []
    second = coordinator.run_candidate(lambda: started.append("candidate"))
    assert second.state is AdmissionState.QUEUED
    assert started == []

    assert first.lease is not None
    first.lease.release()
    third = coordinator.run_candidate(lambda: started.append("candidate"))
    assert started == ["candidate"]
    assert third is None

    # A completed run releases its lease, so the next launch is admitted again.
    fourth = coordinator.acquire("baseline")
    assert fourth.state is AdmissionState.ADMITTED
    assert fourth.lease is not None
    fourth.lease.release()


def test_run_releases_admission_when_startup_fails(tmp_path: Path):
    coordinator = ScorecardAdmissionCoordinator(state_path=tmp_path / "admission")

    with pytest.raises(RuntimeError, match="startup"):
        coordinator.run_baseline(lambda: (_ for _ in ()).throw(RuntimeError("startup")))

    result = coordinator.acquire("candidate")
    assert result.state is AdmissionState.ADMITTED
    assert result.lease is not None
    result.lease.release()


def test_run_releases_admission_when_the_workload_is_cancelled(tmp_path: Path):
    coordinator = ScorecardAdmissionCoordinator(state_path=tmp_path / "admission")

    def cancelled() -> None:
        raise KeyboardInterrupt("workload cancelled")

    with pytest.raises(KeyboardInterrupt):
        coordinator.run_baseline(cancelled)

    result = coordinator.acquire("candidate")
    assert result.state is AdmissionState.ADMITTED
    assert result.lease is not None
    result.lease.release()


def test_from_config_uses_the_configured_bounded_limit(tmp_path: Path):
    coordinator = ScorecardAdmissionCoordinator.from_config(
        ArgusConfig(scorecard_active_workload_limit=2),
        state_path=tmp_path / "admission",
    )
    assert coordinator.max_active_workloads == 2

    baseline = coordinator.acquire("baseline")
    candidate = coordinator.acquire("candidate")
    assert baseline.state is AdmissionState.ADMITTED
    assert candidate.state is AdmissionState.ADMITTED

    overflow = coordinator.acquire("baseline")
    assert overflow.state is AdmissionState.QUEUED
    assert baseline.lease is not None and candidate.lease is not None
    baseline.lease.release()
    candidate.lease.release()
    assert coordinator.acquire("baseline").state is AdmissionState.ADMITTED


def test_workload_context_holds_one_shared_slot_and_releases_a_cancelled_job(tmp_path: Path):
    coordinator = ScorecardAdmissionCoordinator(state_path=tmp_path / "admission")

    with pytest.raises(KeyboardInterrupt):
        with coordinator.workload("baseline") as held:
            assert held.state is AdmissionState.ADMITTED
            assert held.admitted
            blocked = coordinator.acquire("candidate")
            assert blocked.state is AdmissionState.QUEUED
            assert blocked.lease is None
            raise KeyboardInterrupt("candidate cancelled baseline")

    recovered = coordinator.acquire("candidate")
    assert recovered.state is AdmissionState.ADMITTED
    assert recovered.lease is not None
    recovered.lease.release()


def _hold_admission(state_path: str, ready: Event, release: Event) -> None:
    coordinator = ScorecardAdmissionCoordinator(state_path=state_path)
    result = coordinator.acquire("baseline")
    assert result.state is AdmissionState.ADMITTED
    ready.set()
    release.wait(30)


def test_separate_worker_processes_share_admission_and_reclaim_dead_worker(tmp_path: Path):
    state_path = str(tmp_path / "admission")
    ready = Event()
    release = Event()
    worker = Process(target=_hold_admission, args=(state_path, ready, release))
    worker.start()
    assert ready.wait(10)

    coordinator = ScorecardAdmissionCoordinator(state_path=state_path)
    queued = coordinator.acquire("candidate")
    assert queued.state is AdmissionState.QUEUED

    worker.terminate()
    worker.join(10)
    assert not worker.is_alive()

    recovered = coordinator.acquire("candidate")
    assert recovered.state is AdmissionState.ADMITTED
    assert recovered.lease is not None
    recovered.lease.release()


def test_unavailable_result_has_no_host_diagnostics(tmp_path: Path):
    state_path = tmp_path / "admission"
    state_path.write_text("not-json", encoding="utf-8")
    result = ScorecardAdmissionCoordinator(state_path=state_path).acquire("baseline")
    assert result.state is AdmissionState.UNAVAILABLE
    assert result.status == "unavailable"
    assert result.lease is None
    assert not hasattr(result, "reason")
