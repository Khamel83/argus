"""Fail-closed host capacity probes and scorecard admission policy.

The policy is deliberately independent of Docker's object-creation API.  A
caller supplies one already sampled snapshot; a coordinator performs the
sample before waiting and again while holding its creation lock immediately
before invoking the workload factory.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import os
from pathlib import Path
import subprocess
from typing import Protocol


# Stable, content-free values.  Keep these suitable for queue state and logs;
# metric values and exception text never cross the admission interface.
ADMITTED = "admitted"
CPU_CAPACITY_UNAVAILABLE = "cpu_capacity_unavailable"
CPU_CAPACITY_EXCEEDED = "cpu_capacity_exceeded"
MEMORY_CAPACITY_UNAVAILABLE = "memory_capacity_unavailable"
MEMORY_CAPACITY_EXCEEDED = "memory_capacity_exceeded"
DOCKER_CONTAINER_PRESSURE_UNAVAILABLE = "docker_container_pressure_unavailable"
DOCKER_CONTAINER_PRESSURE_EXCEEDED = "docker_container_pressure_exceeded"
DOCKER_NETWORK_PRESSURE_UNAVAILABLE = "docker_network_pressure_unavailable"
DOCKER_NETWORK_PRESSURE_EXCEEDED = "docker_network_pressure_exceeded"
INTERFACE_PRESSURE_UNAVAILABLE = "interface_pressure_unavailable"
INTERFACE_PRESSURE_EXCEEDED = "interface_pressure_exceeded"
ACTIVE_SCORECARD_JOBS_UNAVAILABLE = "active_scorecard_jobs_unavailable"
ACTIVE_SCORECARD_JOBS_EXCEEDED = "active_scorecard_jobs_exceeded"


@dataclass(frozen=True, slots=True)
class CapacityLimits:
    """Validated scorecard capacity budgets.

    Defaults are intentionally conservative: load may reach 85% of one CPU,
    512 MiB must remain available, Docker may contain at most 64 containers
    and 32 networks, the host may expose at most 128 interfaces, and one
    scorecard workload may be active.  Probe commands have a one-second
    deadline.  Every value is bounded in ``__post_init__``.
    """

    max_cpu_load_ratio: float = 0.85
    min_available_memory_bytes: int = 512 * 1024 * 1024
    max_docker_containers: int = 64
    max_docker_networks: int = 32
    max_network_interfaces: int = 128
    max_active_scorecard_jobs: int = 1
    probe_timeout_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_cpu_load_ratio) or not (
            0.0 < self.max_cpu_load_ratio <= 1.0
        ):
            raise ValueError("max_cpu_load_ratio must be in (0, 1]")
        for name in (
            "min_available_memory_bytes",
            "max_docker_containers",
            "max_docker_networks",
            "max_network_interfaces",
            "max_active_scorecard_jobs",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.max_active_scorecard_jobs < 1:
            raise ValueError("max_active_scorecard_jobs must be at least 1")
        if not math.isfinite(self.probe_timeout_seconds) or not (
            0.05 <= self.probe_timeout_seconds <= 10.0
        ):
            raise ValueError("probe_timeout_seconds must be between 0.05 and 10")


# Common names for callers that prefer the policy vocabulary.
CapacityConfig = CapacityLimits
AdmissionConfig = CapacityLimits


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    """One point-in-time host and workload pressure sample.

    ``None`` means that the required probe was unavailable.  The policy does
    not infer safety from a missing metric.
    """

    cpu_load_ratio: float | None
    available_memory_bytes: int | None
    docker_containers: int | None
    docker_networks: int | None
    network_interfaces: int | None
    active_scorecard_jobs: int | None = None

    def with_active_jobs(self, active_scorecard_jobs: int) -> "CapacitySnapshot":
        return replace(self, active_scorecard_jobs=active_scorecard_jobs)


class CapacityProbe(Protocol):
    def sample(self) -> CapacitySnapshot:
        """Return a bounded sample; unavailable metrics must be ``None``."""


class SystemCapacityProbe:
    """Read host and Docker pressure without creating any Docker objects."""

    def __init__(
        self,
        limits: CapacityLimits | None = None,
        *,
        docker_executable: str = "docker",
    ) -> None:
        self._limits = limits or CapacityLimits()
        self._docker_executable = docker_executable

    def sample(self) -> CapacitySnapshot:
        return CapacitySnapshot(
            cpu_load_ratio=self._cpu_load_ratio(),
            available_memory_bytes=self._available_memory_bytes(),
            docker_containers=self._docker_object_count(("ps", "-aq")),
            docker_networks=self._docker_object_count(("network", "ls", "-q")),
            network_interfaces=self._network_interface_count(),
        )

    @staticmethod
    def _cpu_load_ratio() -> float | None:
        try:
            loads = os.getloadavg()
            cpu_count = os.cpu_count() or 0
            if not loads or cpu_count < 1 or not math.isfinite(loads[0]):
                return None
            return max(0.0, loads[0] / cpu_count)
        except (AttributeError, OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _available_memory_bytes() -> int | None:
        try:
            for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    if len(parts) < 2:
                        return None
                    value = int(parts[1])
                    return value * 1024 if value >= 0 else None
        except (OSError, UnicodeError, ValueError):
            return None
        return None

    def _docker_object_count(self, args: tuple[str, ...]) -> int | None:
        try:
            result = subprocess.run(
                [self._docker_executable, *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=self._limits.probe_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return sum(bool(line.strip()) for line in result.stdout.splitlines())

    @staticmethod
    def _network_interface_count() -> int | None:
        try:
            return sum(1 for entry in Path("/sys/class/net").iterdir() if entry.name)
        except OSError:
            return None


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """The only result exposed by the policy: allow or stable reason code."""

    admitted: bool
    reason_code: str

    @property
    def allowed(self) -> bool:
        return self.admitted


class ScorecardAdmissionPolicy:
    """Evaluate every required capacity metric in a fixed fail-closed order."""

    def __init__(self, limits: CapacityLimits | None = None) -> None:
        self.limits = limits or CapacityLimits()

    def evaluate(self, snapshot: CapacitySnapshot) -> AdmissionDecision:
        checks = (
            (
                snapshot.cpu_load_ratio,
                CPU_CAPACITY_UNAVAILABLE,
                CPU_CAPACITY_EXCEEDED,
                lambda value: math.isfinite(value)
                and 0.0 <= value <= self.limits.max_cpu_load_ratio,
            ),
            (
                snapshot.available_memory_bytes,
                MEMORY_CAPACITY_UNAVAILABLE,
                MEMORY_CAPACITY_EXCEEDED,
                lambda value: value >= self.limits.min_available_memory_bytes,
            ),
            (
                snapshot.docker_containers,
                DOCKER_CONTAINER_PRESSURE_UNAVAILABLE,
                DOCKER_CONTAINER_PRESSURE_EXCEEDED,
                lambda value: value <= self.limits.max_docker_containers,
            ),
            (
                snapshot.docker_networks,
                DOCKER_NETWORK_PRESSURE_UNAVAILABLE,
                DOCKER_NETWORK_PRESSURE_EXCEEDED,
                lambda value: value <= self.limits.max_docker_networks,
            ),
            (
                snapshot.network_interfaces,
                INTERFACE_PRESSURE_UNAVAILABLE,
                INTERFACE_PRESSURE_EXCEEDED,
                lambda value: value <= self.limits.max_network_interfaces,
            ),
            (
                snapshot.active_scorecard_jobs,
                ACTIVE_SCORECARD_JOBS_UNAVAILABLE,
                ACTIVE_SCORECARD_JOBS_EXCEEDED,
                lambda value: value <= self.limits.max_active_scorecard_jobs - 1,
            ),
        )
        for value, unavailable, exceeded, within_budget in checks:
            if value is None or not isinstance(value, (int, float)) or isinstance(value, bool):
                return AdmissionDecision(False, unavailable)
            if isinstance(value, float) and not math.isfinite(value):
                return AdmissionDecision(False, unavailable)
            if value < 0:
                return AdmissionDecision(False, unavailable)
            if not within_budget(value):
                return AdmissionDecision(False, exceeded)
        return AdmissionDecision(True, ADMITTED)

    admit = evaluate


__all__ = [
    "ADMITTED",
    "ACTIVE_SCORECARD_JOBS_EXCEEDED",
    "ACTIVE_SCORECARD_JOBS_UNAVAILABLE",
    "AdmissionConfig",
    "AdmissionDecision",
    "CapacityConfig",
    "CapacityLimits",
    "CapacityProbe",
    "CapacitySnapshot",
    "CPU_CAPACITY_EXCEEDED",
    "CPU_CAPACITY_UNAVAILABLE",
    "DOCKER_CONTAINER_PRESSURE_EXCEEDED",
    "DOCKER_CONTAINER_PRESSURE_UNAVAILABLE",
    "DOCKER_NETWORK_PRESSURE_EXCEEDED",
    "DOCKER_NETWORK_PRESSURE_UNAVAILABLE",
    "INTERFACE_PRESSURE_EXCEEDED",
    "INTERFACE_PRESSURE_UNAVAILABLE",
    "MEMORY_CAPACITY_EXCEEDED",
    "MEMORY_CAPACITY_UNAVAILABLE",
    "ScorecardAdmissionPolicy",
    "SystemCapacityProbe",
]
