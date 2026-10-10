"""Host-scoped admission for live scorecard workloads.

The live scorecard launcher owns Docker/container lifecycle.  It must use one
instance of :class:`ScorecardAdmissionCoordinator` for both baseline and
candidate launches, and acquire a lease before creating any workload
resource.  The lease is represented by a small, locked state file so separate
worker processes on the same host share one capacity budget.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import errno
import fcntl
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
from collections.abc import Callable, Iterator
from typing import Any


SCORECARD_ACTIVE_WORKLOAD_LIMIT_ENV = "ARGUS_SCORECARD_ACTIVE_WORKLOAD_LIMIT"
DEFAULT_SCORECARD_ACTIVE_WORKLOAD_LIMIT = 1
MAX_SCORECARD_ACTIVE_WORKLOAD_LIMIT = 32
DEFAULT_SCORECARD_ADMISSION_STATE = (
    Path(tempfile.gettempdir()) / "argus-scorecard-admission-v1"
)
_STATE_VERSION = 1
_MAX_STATE_BYTES = 256 * 1024


class AdmissionState(str, Enum):
    """Stable public outcomes of a workload admission attempt."""

    ADMITTED = "admitted"
    QUEUED = "queued"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    """A privacy-safe admission outcome and optional workload lease."""

    state: AdmissionState
    lease: "ScorecardAdmissionLease | None" = None

    @property
    def status(self) -> str:
        """Return the stable wire value without host-specific diagnostics."""

        return self.state.value

    @property
    def admitted(self) -> bool:
        return self.state is AdmissionState.ADMITTED


@dataclass(frozen=True, slots=True)
class _Owner:
    pid: int
    process_start: str


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


def _validate_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("scorecard active workload limit must be an integer")
    if not 1 <= value <= MAX_SCORECARD_ACTIVE_WORKLOAD_LIMIT:
        raise ValueError(
            "scorecard active workload limit must be between 1 and "
            f"{MAX_SCORECARD_ACTIVE_WORKLOAD_LIMIT}"
        )
    return value


def configured_scorecard_active_workload_limit(
    environ: dict[str, str] | None = None,
) -> int:
    """Read and validate the host workload limit without resolving secrets."""

    source = os.environ if environ is None else environ
    raw = source.get(SCORECARD_ACTIVE_WORKLOAD_LIMIT_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_SCORECARD_ACTIVE_WORKLOAD_LIMIT
    try:
        value = int(raw.strip(), 10)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{SCORECARD_ACTIVE_WORKLOAD_LIMIT_ENV} must be a bounded positive integer"
        ) from exc
    return _validate_limit(value)


def _process_start_token(pid: int) -> str | None:
    """Return Linux's process-start token, preventing PID-reuse reclamation."""

    try:
        contents = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None
    closing = contents.rfind(")")
    if closing < 0:
        return None
    fields = contents[closing + 2 :].split()
    if len(fields) <= 19:
        return None
    return fields[19]


def _current_owner() -> _Owner:
    pid = os.getpid()
    return _Owner(pid=pid, process_start=_process_start_token(pid) or f"pid:{pid}")


def _owner_alive(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    pid = record.get("pid")
    process_start = record.get("process_start")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if not isinstance(process_start, str) or not process_start:
        return False
    current = _process_start_token(pid)
    if current is not None:
        return current == process_start
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def _safe_mode(mode: str) -> str:
    if mode not in {"baseline", "candidate"}:
        raise ValueError("scorecard workload mode must be baseline or candidate")
    return mode


class ScorecardAdmissionLease:
    """Idempotent lease held until the workload completes or is cancelled."""

    __slots__ = ("_coordinator", "_token", "mode", "_released")

    def __init__(
        self,
        coordinator: "ScorecardAdmissionCoordinator",
        token: str,
        mode: str,
    ) -> None:
        self._coordinator = coordinator
        self._token = token
        self.mode = mode
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._coordinator.release(self)

    def __enter__(self) -> "ScorecardAdmissionLease":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class ScorecardAdmissionCoordinator:
    """Atomically coordinate baseline and candidate capacity on one host."""

    def __init__(
        self,
        max_active_workloads: int | None = None,
        *,
        state_path: str | os.PathLike[str] | None = None,
    ) -> None:
        if max_active_workloads is None:
            max_active_workloads = configured_scorecard_active_workload_limit()
        self.max_active_workloads = _validate_limit(max_active_workloads)
        configured_path = os.environ.get("ARGUS_SCORECARD_ADMISSION_STATE", "")
        self.state_path = Path(
            state_path
            or configured_path
            or DEFAULT_SCORECARD_ADMISSION_STATE
        ).expanduser()
        self.lock_path = self.state_path.with_name(f"{self.state_path.name}.lock")

    @classmethod
    def from_config(cls, config: Any, *, state_path: str | os.PathLike[str] | None = None):
        """Build a coordinator from an ``ArgusConfig``-like object."""

        return cls(
            max_active_workloads=config.scorecard_active_workload_limit,
            state_path=state_path,
        )

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with _thread_lock(self.lock_path):
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _read_state(self) -> list[dict[str, object]]:
        try:
            if not self.state_path.exists():
                return []
            if self.state_path.stat().st_size > _MAX_STATE_BYTES:
                raise ValueError("state too large")
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("admission state unavailable") from exc
        if not isinstance(raw, dict) or raw.get("version") != _STATE_VERSION:
            raise RuntimeError("admission state unavailable")
        leases = raw.get("leases")
        if not isinstance(leases, list):
            raise RuntimeError("admission state unavailable")
        if any(not isinstance(item, dict) for item in leases):
            raise RuntimeError("admission state unavailable")
        return [item for item in leases if _owner_alive(item)]

    def _write_state(self, leases: list[dict[str, object]]) -> None:
        payload = json.dumps(
            {"version": _STATE_VERSION, "leases": leases},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > _MAX_STATE_BYTES:
            raise RuntimeError("admission state unavailable")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.state_path.parent,
                prefix=f".{self.state_path.name}.",
                delete=False,
            ) as stream:
                temporary = stream.name
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.state_path)
        except OSError as exc:
            raise RuntimeError("admission state unavailable") from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def acquire(self, mode: str) -> AdmissionResult:
        """Try to acquire a lease without starting any workload resource."""

        mode = _safe_mode(mode)
        token = secrets.token_hex(16)
        owner = _current_owner()
        try:
            with self._locked():
                leases = self._read_state()
                if len(leases) >= self.max_active_workloads:
                    self._write_state(leases)
                    return AdmissionResult(AdmissionState.QUEUED)
                leases.append(
                    {
                        "token": token,
                        "mode": mode,
                        "pid": owner.pid,
                        "process_start": owner.process_start,
                    }
                )
                self._write_state(leases)
        except (OSError, RuntimeError):
            return AdmissionResult(AdmissionState.UNAVAILABLE)
        return AdmissionResult(
            AdmissionState.ADMITTED,
            ScorecardAdmissionLease(self, token, mode),
        )

    def release(self, lease: ScorecardAdmissionLease) -> None:
        """Release a lease; repeated and late releases are harmless."""

        try:
            with self._locked():
                leases = self._read_state()
                remaining = [
                    item for item in leases if item.get("token") != lease._token
                ]
                if len(remaining) != len(leases):
                    self._write_state(remaining)
        except (OSError, RuntimeError):
            # A dead worker's lease is reclaimed on the next acquire.  Release
            # must not turn successful/cancelled workload cleanup into a new
            # failure if the host state has already disappeared.
            return

    @contextmanager
    def workload(self, mode: str) -> Iterator[AdmissionResult]:
        """Yield admission and always release an admitted workload."""

        result = self.acquire(mode)
        try:
            yield result
        finally:
            if result.lease is not None:
                result.lease.release()

    def run(
        self,
        mode: str,
        startup: Callable[[], Any],
    ) -> Any:
        """Run startup while admitted, releasing on every exit path.

        ``startup`` is the caller's Docker/container/network/exec setup.  It is
        deliberately invoked only after the shared lease is admitted.
        """

        result = self.acquire(mode)
        if not result.admitted:
            return result
        lease = result.lease
        if lease is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("admitted workload has no lease")
        try:
            return startup()
        finally:
            lease.release()

    def run_baseline(self, startup: Callable[[], Any]) -> Any:
        """Run baseline startup through the shared host admission budget."""

        return self.run("baseline", startup)

    def run_candidate(self, startup: Callable[[], Any]) -> Any:
        """Run candidate startup through the shared host admission budget."""

        return self.run("candidate", startup)


__all__ = (
    "AdmissionResult",
    "AdmissionState",
    "DEFAULT_SCORECARD_ACTIVE_WORKLOAD_LIMIT",
    "MAX_SCORECARD_ACTIVE_WORKLOAD_LIMIT",
    "SCORECARD_ACTIVE_WORKLOAD_LIMIT_ENV",
    "ScorecardAdmissionCoordinator",
    "ScorecardAdmissionLease",
    "configured_scorecard_active_workload_limit",
)
