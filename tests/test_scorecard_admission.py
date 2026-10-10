"""Boundaries and side-effect guards for scorecard workload admission."""

from __future__ import annotations

from dataclasses import replace

import pytest

from argus.config import load_config
from argus.scorecard.admission import (
    ACTIVE_SCORECARD_JOBS_EXCEEDED,
    CapacityLimits,
    CapacitySnapshot,
    ScorecardAdmissionPolicy,
)
from argus.scorecard.coordinator import ScorecardCoordinator


BASE = CapacitySnapshot(
    cpu_load_ratio=0.2,
    available_memory_bytes=2 * 1024 * 1024 * 1024,
    docker_containers=2,
    docker_networks=2,
    network_interfaces=4,
    active_scorecard_jobs=0,
)


def test_exact_capacity_boundaries_are_admitted():
    limits = CapacityLimits()
    policy = ScorecardAdmissionPolicy(limits)

    assert policy.evaluate(
        CapacitySnapshot(
            cpu_load_ratio=limits.max_cpu_load_ratio,
            available_memory_bytes=limits.min_available_memory_bytes,
            docker_containers=limits.max_docker_containers,
            docker_networks=limits.max_docker_networks,
            network_interfaces=limits.max_network_interfaces,
            active_scorecard_jobs=limits.max_active_scorecard_jobs - 1,
        )
    ).admitted


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("cpu_load_ratio", 0.86, "cpu_capacity_exceeded"),
        ("available_memory_bytes", 512 * 1024 * 1024 - 1, "memory_capacity_exceeded"),
        ("docker_containers", 65, "docker_container_pressure_exceeded"),
        ("docker_networks", 33, "docker_network_pressure_exceeded"),
        ("network_interfaces", 129, "interface_pressure_exceeded"),
        ("active_scorecard_jobs", 1, ACTIVE_SCORECARD_JOBS_EXCEEDED),
    ],
)
def test_each_pressure_boundary_rejects_without_resource_creation(
    field, value, reason
):
    snapshot = replace(BASE, **{field: value})
    decision = ScorecardAdmissionPolicy().evaluate(snapshot)
    assert (decision.admitted, decision.reason_code) == (False, reason)

    resources = {"container": 0, "network": 0, "exec": 0}

    def create_workload():
        resources["network"] += 1
        resources["container"] += 1
        resources["exec"] += 1
        return object()

    coordinator = ScorecardCoordinator(
        ScorecardAdmissionPolicy(),
        _Probe(BASE),
    )
    if field == "active_scorecard_jobs":
        first = coordinator.start_workload(create_workload)
        assert first.admitted
        result = coordinator.start_workload(create_workload)
    else:
        result = ScorecardCoordinator(
            ScorecardAdmissionPolicy(),
            _Probe(snapshot),
        ).start_workload(create_workload)
    assert not result.admitted
    assert resources == (
        {"container": 0, "network": 0, "exec": 0}
        if field != "active_scorecard_jobs"
        else {"container": 1, "network": 1, "exec": 1}
    )


@pytest.mark.parametrize(
    "field",
    (
        "cpu_load_ratio",
        "available_memory_bytes",
        "docker_containers",
        "docker_networks",
        "network_interfaces",
        "active_scorecard_jobs",
    ),
)
def test_missing_required_probe_fails_closed(field):
    decision = ScorecardAdmissionPolicy().evaluate(
        replace(BASE, **{field: None})
    )
    assert not decision.admitted
    assert decision.reason_code.endswith("_unavailable")

def test_capacity_is_rechecked_after_wait_and_before_creation():
    clock = [0.0]
    probe = _Probe(
        replace(BASE, docker_containers=65),
        BASE,
        BASE,
    )
    created = []

    def sleep(seconds):
        clock[0] += seconds

    coordinator = ScorecardCoordinator(
        ScorecardAdmissionPolicy(),
        probe,
        clock=lambda: clock[0],
        sleeper=sleep,
    )
    queued = coordinator.start_workload(
        lambda: created.append("created"),
        wait_timeout_seconds=1.0,
        wait_interval_seconds=0.5,
    )
    assert queued.admitted
    assert created == ["created"]
    queued.release()

    # A workload can pass its initial check but must not start when the
    # immediate pre-creation sample crosses a pressure boundary.
    probe = _Probe(BASE, replace(BASE, docker_networks=33))
    coordinator = ScorecardCoordinator(ScorecardAdmissionPolicy(), probe)
    rejected = coordinator.start_workload(lambda: created.append("bad"))
    assert not rejected.admitted
    assert rejected.decision.reason_code == "docker_network_pressure_exceeded"
    assert created == ["created"]


def test_scorecard_capacity_configuration_has_safe_defaults_and_validation():
    defaults = load_config(environ={"ARGUS_DISABLE_SECRET_RESOLUTION": "true"})
    assert defaults.scorecard_capacity == CapacityLimits()

    configured = load_config(
        environ={
            "ARGUS_DISABLE_SECRET_RESOLUTION": "true",
            "ARGUS_SCORECARD_MAX_CPU_LOAD_RATIO": "0.7",
            "ARGUS_SCORECARD_MAX_ACTIVE_JOBS": "2",
        }
    )
    assert configured.scorecard_capacity.max_cpu_load_ratio == 0.7
    assert configured.scorecard_capacity.max_active_scorecard_jobs == 2

    with pytest.raises(ValueError, match="max_cpu_load_ratio"):
        load_config(
            environ={
                "ARGUS_DISABLE_SECRET_RESOLUTION": "true",
                "ARGUS_SCORECARD_MAX_CPU_LOAD_RATIO": "1.2",
            }
        )


class _Probe:
    def __init__(self, *snapshots):
        self.snapshots = list(snapshots)

    def sample(self):
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]
