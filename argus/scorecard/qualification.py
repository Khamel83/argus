"""Disposable qualification harness for scorecard admission protection.

The harness deliberately runs injected local commands.  It never constructs an
Argus broker, opens a database, or talks to a deployment.  A command is given a
sanitized environment and the qualification run id so a disposable scorecard
adapter can label every resource it creates.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


EVIDENCE_DEADLINE_SECONDS = 5.0
EVIDENCE_NAMES = ("docker", "hermes", "homelab")
_PRESSURE_DEADLINE_SECONDS = 5.0
_TERMINAL_EVIDENCE_STATUSES = frozenset(
    {"failed_closed", "timeout", "timed_out", "error", "unavailable"}
)


class QualificationError(ValueError):
    """The qualification configuration or result is unsafe or incomplete."""


@dataclass(frozen=True)
class QualificationConfig:
    """Commands and bounds for one disposable qualification run."""

    output: Path
    baseline_command: tuple[str, ...]
    candidate_command: tuple[str, ...]
    evidence_commands: Mapping[str, tuple[str, ...]]
    pressure_command: tuple[str, ...] | None = None
    cleanup_command: tuple[str, ...] | None = None
    scorecard_timeout: float = 120.0
    scorecard_concurrency: int = 1
    pressure_interval: float = 0.25
    hermetic_defaults: bool = False

    def __post_init__(self) -> None:
        if set(self.evidence_commands) != set(EVIDENCE_NAMES):
            raise QualificationError(
                "evidence commands must contain exactly docker, hermes, and homelab"
            )
        if self.scorecard_concurrency < 1 or self.scorecard_concurrency > 2:
            raise QualificationError("scorecard concurrency must be between 1 and 2")
        if self.scorecard_timeout <= 0 or self.scorecard_timeout > 600:
            raise QualificationError("scorecard timeout must be between 0 and 600 seconds")
        if self.pressure_interval <= 0 or self.pressure_interval > 5:
            raise QualificationError("pressure interval must be between 0 and 5 seconds")
        if self.cleanup_command is None and not self.hermetic_defaults:
            raise QualificationError(
                "custom scorecard commands require --cleanup-command"
            )


def parse_command(value: str, *, label: str) -> tuple[str, ...]:
    """Parse one explicit argv command without invoking a shell."""

    try:
        command = tuple(shlex.split(value))
    except ValueError as exc:
        raise QualificationError(f"invalid {label} command: {exc}") from exc
    if not command:
        raise QualificationError(f"{label} command must not be empty")
    return command


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_configuration(output: Path) -> QualificationConfig:
    """Return a no-network qualification using the real hermetic scorecard path."""

    script = _root() / "scripts" / "run-scorecard.py"
    baseline = (sys.executable, str(script), "--lane", "hermetic", "--output", "{output}")
    candidate = (sys.executable, str(script), "--lane", "hermetic", "--output", "{output}")
    return QualificationConfig(
        output=output,
        baseline_command=baseline,
        candidate_command=candidate,
        evidence_commands={name: ("false",) for name in EVIDENCE_NAMES},
        hermetic_defaults=True,
    )


def _render(command: Sequence[str], values: Mapping[str, str]) -> tuple[str, ...]:
    rendered: list[str] = []
    for token in command:
        for key, value in values.items():
            token = token.replace("{" + key + "}", value)
        rendered.append(token)
    return tuple(rendered)


def _qualification_environment(run_id: str, output: Path) -> dict[str, str]:
    """Build an allow-list environment; credentials and production URLs never pass through."""

    environment: dict[str, str] = {}
    for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "VIRTUAL_ENV"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    environment["PYTHONPATH"] = str(_root())
    environment.update(
        {
            "ARGUS_AUTOLOAD_DOTENV": "false",
            "ARGUS_ENV": "development",
            "ARGUS_MCP_STANDALONE": "true",
            "ARGUS_DB_URL": f"sqlite:///{(output / 'qualification.db').resolve()}",
            "ARGUS_QUALIFICATION_DISPOSABLE": "true",
            "ARGUS_SCORECARD_QUALIFICATION_ID": run_id,
            "ARGUS_SCORECARD_QUALIFICATION_OUTPUT": str(output.resolve()),
        }
    )
    return environment


def _run_process(
    command: Sequence[str],
    *,
    environment: Mapping[str, str],
    timeout: float,
    processes: set[subprocess.Popen[str]],
    processes_lock: threading.Lock,
) -> dict[str, Any]:
    started = time.monotonic()
    process = subprocess.Popen(
        list(command),
        cwd=_root(),
        env=dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    with processes_lock:
        processes.add(process)
    timed_out = False
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            stdout, stderr = process.communicate()
        return {
            "returncode": None if timed_out else process.returncode,
            "timed_out": timed_out,
            "stdout": stdout[-4000:],
            "stderr": stderr[-4000:],
            "latency_seconds": round(time.monotonic() - started, 6),
        }
    finally:
        with processes_lock:
            processes.discard(process)


def _terminate_processes(
    processes: set[subprocess.Popen[str]], processes_lock: threading.Lock
) -> None:
    with processes_lock:
        active = tuple(processes)
    for process in active:
        if process.poll() is None:
            process.kill()
    for process in active:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


def _decode_json_output(output: str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise QualificationError(f"{label} did not return JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise QualificationError(f"{label} must return a JSON object")
    return value


def _default_pressure_snapshot() -> dict[str, Any]:
    """Read-only local Docker and interface pressure without requiring Docker."""

    interfaces_path = Path("/sys/class/net")
    interfaces = sorted(path.name for path in interfaces_path.iterdir()) if interfaces_path.is_dir() else []
    veth_count = sum(name.startswith("veth") for name in interfaces)
    snapshot: dict[str, Any] = {
        "available": True,
        "docker_available": shutil.which("docker") is not None,
        "docker_containers": None,
        "docker_networks": None,
        "interfaces": len(interfaces),
        "veth_interfaces": veth_count,
    }
    if not snapshot["docker_available"]:
        snapshot["available"] = False
        snapshot["reason"] = "docker_cli_unavailable"
        return snapshot
    try:
        containers = subprocess.run(
            ["docker", "ps", "-aq"], capture_output=True, text=True, timeout=5, check=False
        )
        networks = subprocess.run(
            ["docker", "network", "ls", "-q"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        snapshot.update(available=False, reason=type(exc).__name__)
        return snapshot
    if containers.returncode or networks.returncode:
        snapshot.update(available=False, reason="docker_probe_failed")
        return snapshot
    snapshot["docker_containers"] = len(containers.stdout.splitlines())
    snapshot["docker_networks"] = len(networks.stdout.splitlines())
    return snapshot


def _pressure_snapshot(
    command: Sequence[str] | None,
    *,
    phase: str,
    environment: Mapping[str, str],
    processes: set[subprocess.Popen[str]],
    processes_lock: threading.Lock,
) -> dict[str, Any]:
    if command is None:
        return _default_pressure_snapshot()
    try:
        result = _run_process(
            _render(command, {"phase": phase}),
            environment=environment,
            timeout=_PRESSURE_DEADLINE_SECONDS,
            processes=processes,
            processes_lock=processes_lock,
        )
    except OSError:
        return {"available": False, "reason": "pressure_probe_unavailable", "phase": phase}
    if result["timed_out"] or result["returncode"] != 0:
        return {"available": False, "reason": "pressure_probe_failed", "phase": phase}
    try:
        value = _decode_json_output(result["stdout"], label=f"pressure probe ({phase})")
    except QualificationError as exc:
        return {"available": False, "reason": str(exc), "phase": phase}
    value.setdefault("available", True)
    return value


def _stop_pressure_sampler(stop_sampling: threading.Event, sampler: threading.Thread) -> None:
    """Stop sampling before process teardown, so an in-flight probe is not aborted and misreported."""

    stop_sampling.set()
    sampler.join(timeout=_PRESSURE_DEADLINE_SECONDS + 1)


def _evidence_failed_closed(stdout: str) -> bool:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(value, dict):
        return False
    status = value.get("status")
    return status in _TERMINAL_EVIDENCE_STATUSES or value.get("ok") is False


def _run_evidence(
    name: str,
    command: Sequence[str],
    *,
    environment: Mapping[str, str],
    processes: set[subprocess.Popen[str]],
    processes_lock: threading.Lock,
) -> dict[str, Any]:
    try:
        result = _run_process(
            command,
            environment=environment,
            timeout=EVIDENCE_DEADLINE_SECONDS,
            processes=processes,
            processes_lock=processes_lock,
        )
    except OSError as exc:
        result = {
            "returncode": None,
            "timed_out": False,
            "stdout": "",
            "stderr": str(exc),
            "latency_seconds": 0.0,
        }
    failed_closed = result["timed_out"] or result["returncode"] != 0 or _evidence_failed_closed(
        result["stdout"]
    )
    result.update(
        {
            "name": name,
            "deadline_seconds": EVIDENCE_DEADLINE_SECONDS,
            "ok": not failed_closed,
            "failure": (
                "deadline_exceeded"
                if result["timed_out"]
                else "failed_closed"
                if failed_closed
                else None
            ),
        }
    )
    return result


class _ScorecardQueue:
    def __init__(self, limit: int, transitions: list[dict[str, Any]]) -> None:
        self._limit = limit
        self._transitions = transitions
        self._condition = threading.Condition()
        self._active = 0
        self._max_active = 0
        self._pending = 0
        self._stop = False

    @property
    def active(self) -> int:
        with self._condition:
            return self._active

    @property
    def max_active(self) -> int:
        with self._condition:
            return self._max_active

    @property
    def pending(self) -> int:
        with self._condition:
            return self._pending

    def _transition(self, workload: str, status: str) -> None:
        self._transitions.append(
            {
                "workload": workload,
                "status": status,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def acquire(self, workload: str) -> bool:
        with self._condition:
            self._pending += 1
            self._transition(workload, "queued")
            while self._active >= self._limit and not self._stop:
                self._condition.wait(timeout=0.1)
            self._pending -= 1
            if self._stop:
                self._transition(workload, "canceled")
                return False
            self._active += 1
            self._max_active = max(self._max_active, self._active)
            self._transition(workload, "admitted")
            return True

    def release(self, workload: str, status: str) -> None:
        with self._condition:
            self._active -= 1
            self._transition(workload, status)
            self._transition(workload, "released")
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._stop = True
            self._condition.notify_all()


def _aggregate_pressure(before: dict[str, Any], during: list[dict[str, Any]], after: dict[str, Any]) -> dict[str, Any]:
    numeric: dict[str, list[float]] = {}
    for snapshot in (before, *during, after):
        for key, value in snapshot.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric.setdefault(key, []).append(float(value))
    return {
        "before": before,
        "during": during,
        "after": after,
        "maximum": {key: max(values) for key, values in numeric.items()},
    }


def _run_cleanup(
    config: QualificationConfig,
    *,
    run_id: str,
    output: Path,
    environment: Mapping[str, str],
    processes: set[subprocess.Popen[str]],
    processes_lock: threading.Lock,
) -> dict[str, Any]:
    if config.cleanup_command is None:
        return {
            "status": "verified",
            "mode": "hermetic_no_network",
            "containers": 0,
            "networks": 0,
            "leases": 0,
            "queue_entries": 0,
        }
    try:
        result = _run_process(
            _render(config.cleanup_command, {"run_id": run_id, "output": str(output)}),
            environment=environment,
            timeout=EVIDENCE_DEADLINE_SECONDS,
            processes=processes,
            processes_lock=processes_lock,
        )
    except OSError as exc:
        result = {
            "returncode": None,
            "timed_out": False,
            "stdout": "",
            "stderr": str(exc),
            "latency_seconds": 0.0,
        }
    clean = result["returncode"] == 0 and not result["timed_out"]
    payload: dict[str, Any] = {}
    if clean and result["stdout"].strip():
        try:
            payload = _decode_json_output(result["stdout"], label="cleanup command")
        except QualificationError:
            clean = False
    if payload.get("clean") is False or payload.get("remaining"):
        clean = False
    return {
        "status": "verified" if clean else "failed",
        "mode": "injected_local_cleanup",
        "containers": payload.get("containers", 0),
        "networks": payload.get("networks", 0),
        "leases": payload.get("leases", 0),
        "queue_entries": payload.get("queue_entries", 0),
        "command": result,
    }


def run_qualification(config: QualificationConfig) -> dict[str, Any]:
    """Run scorecard workloads and local evidence calls, returning a JSON-safe report."""

    output = config.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_id = f"qualification-{uuid.uuid4().hex}"
    environment = _qualification_environment(run_id, output)
    processes: set[subprocess.Popen[str]] = set()
    processes_lock = threading.Lock()
    transitions: list[dict[str, Any]] = []
    queue = _ScorecardQueue(config.scorecard_concurrency, transitions)
    started_at = datetime.now(timezone.utc)
    stop_sampling = threading.Event()
    pressure_during: list[dict[str, Any]] = []
    pressure_error: str | None = None

    before = _pressure_snapshot(
        config.pressure_command,
        phase="before",
        environment=environment,
        processes=processes,
        processes_lock=processes_lock,
    )

    def sample_pressure() -> None:
        nonlocal pressure_error
        while not stop_sampling.wait(config.pressure_interval):
            snapshot = _pressure_snapshot(
                config.pressure_command,
                phase="during",
                environment=environment,
                processes=processes,
                processes_lock=processes_lock,
            )
            pressure_during.append(snapshot)
            if snapshot.get("available") is False:
                pressure_error = "pressure_probe_failed"

    sampler = threading.Thread(target=sample_pressure, name="scorecard-pressure", daemon=True)
    sampler.start()

    def scorecard(workload: str, command: Sequence[str], destination: Path) -> dict[str, Any]:
        if not queue.acquire(workload):
            return {"workload": workload, "status": "canceled", "ok": False}
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                result = _run_process(
                    _render(
                        command,
                        {"output": str(destination), "run_id": run_id, "workload": workload},
                    ),
                    environment=environment,
                    timeout=config.scorecard_timeout,
                    processes=processes,
                    processes_lock=processes_lock,
                )
            except OSError as exc:
                result = {
                    "returncode": None,
                    "timed_out": False,
                    "stdout": "",
                    "stderr": str(exc),
                    "latency_seconds": 0.0,
                }
            ok = result["returncode"] == 0 and not result["timed_out"]
            queue.release(workload, "completed" if ok else "failed")
            return {
                "workload": workload,
                "status": "completed" if ok else "failed",
                "ok": ok,
                **result,
            }
        except BaseException:
            queue.release(workload, "failed")
            raise

    workloads = {
        "baseline": (config.baseline_command, output / "baseline"),
        "candidate": (config.candidate_command, output / "candidate"),
    }
    scorecard_results: dict[str, dict[str, Any]] = {}
    evidence_results: dict[str, dict[str, Any]] = {}
    scorecard_futures: dict[Future[Any], str] = {}
    evidence_futures: dict[Future[Any], str] = {}
    interrupted = False
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scorecard")
    evidence_executor = ThreadPoolExecutor(max_workers=len(EVIDENCE_NAMES), thread_name_prefix="evidence")
    try:
        for name, (command, destination) in workloads.items():
            scorecard_futures[executor.submit(scorecard, name, command, destination)] = name
        for name, command in config.evidence_commands.items():
            evidence_futures[
                evidence_executor.submit(
                    _run_evidence,
                    name,
                    command,
                    environment=environment,
                    processes=processes,
                    processes_lock=processes_lock,
                )
            ] = name
        for future in as_completed((*scorecard_futures, *evidence_futures)):
            if future in scorecard_futures:
                scorecard_results[scorecard_futures[future]] = future.result()
            else:
                evidence_results[evidence_futures[future]] = future.result()
    except KeyboardInterrupt:
        interrupted = True
        queue.stop()
        _stop_pressure_sampler(stop_sampling, sampler)
        _terminate_processes(processes, processes_lock)
        for future in (*scorecard_futures, *evidence_futures):
            future.cancel()
    finally:
        queue.stop()
        _stop_pressure_sampler(stop_sampling, sampler)
        _terminate_processes(processes, processes_lock)
        executor.shutdown(wait=True, cancel_futures=True)
        evidence_executor.shutdown(wait=True, cancel_futures=True)

    cleanup = _run_cleanup(
        config,
        run_id=run_id,
        output=output,
        environment=environment,
        processes=processes,
        processes_lock=processes_lock,
    )
    after = _pressure_snapshot(
        config.pressure_command,
        phase="after",
        environment=environment,
        processes=processes,
        processes_lock=processes_lock,
    )
    pressure = _aggregate_pressure(before, pressure_during, after)
    max_evidence_latency = max(
        (result.get("latency_seconds", 0.0) for result in evidence_results.values()),
        default=0.0,
    )
    evidence_ok = set(evidence_results) == set(EVIDENCE_NAMES) and all(
        result.get("ok") is True for result in evidence_results.values()
    )
    scorecard_ok = set(scorecard_results) == set(workloads) and all(
        result.get("ok") is True for result in scorecard_results.values()
    )
    pressure_ok = (
        pressure_error is None
        and before.get("available", True) is not False
        and after.get("available", True) is not False
    )
    cleanup_ok = (
        cleanup.get("status") == "verified"
        and cleanup.get("containers", 0) == 0
        and cleanup.get("networks", 0) == 0
        and cleanup.get("leases", 0) == 0
        and cleanup.get("queue_entries", 0) == 0
    )
    report = {
        "schema": "argus-scorecard-qualification-v1",
        "status": "interrupted" if interrupted else "pass" if all((evidence_ok, scorecard_ok, pressure_ok, cleanup_ok)) else "fail",
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "disposable": True,
        "evidence_deadline_seconds": EVIDENCE_DEADLINE_SECONDS,
        "max_evidence_latency_seconds": max_evidence_latency,
        "evidence": evidence_results,
        "scorecards": scorecard_results,
        "queue": {
            "limit": config.scorecard_concurrency,
            "max_active": queue.max_active,
            "active_after": queue.active,
            "pending_after": queue.pending,
            "transitions": transitions,
        },
        "pressure": pressure,
        "cleanup": cleanup,
        "checks": {
            "evidence_within_deadline": evidence_ok and max_evidence_latency <= EVIDENCE_DEADLINE_SECONDS,
            "scorecards_completed": scorecard_ok,
            "pressure_available": pressure_ok,
            "resources_clean": cleanup_ok and queue.active == 0 and queue.pending == 0,
        },
    }
    temporary = output / ".qualification.json.tmp"
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output / "qualification.json")
    return report
