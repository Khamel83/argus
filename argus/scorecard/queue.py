"""Bounded, cancellable admission for scorecard workloads.

The queue owns the admission-to-cleanup lifecycle.  A workload callback is not
called until admission succeeds, so waiting work cannot initialize Docker or
any other ephemeral resources.  The queue exposes only workload state and
bounded retry metadata; host capacity details stay behind the admission
callback.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
import inspect
import math
from typing import Any, Generic, TypeVar


ResultT = TypeVar("ResultT")
AdmissionCallback = Callable[
    [str],
    "AdmissionLease | bool | None | Awaitable[AdmissionLease | bool | None]",
]
WorkCallback = Callable[[], ResultT | Awaitable[ResultT]]
ReleaseCallback = Callable[[], None | Awaitable[None]]


class QueueStatus(StrEnum):
    """Public lifecycle states for one queued workload."""

    WAITING = "waiting"
    ADMITTED = "admitted"
    CANCELED = "canceled"
    FAILED = "failed"
    COMPLETED = "completed"


class CapacityDenied(Exception):
    """The admission callback cannot grant capacity yet."""


class QueueFull(RuntimeError):
    """The bounded queue cannot accept another workload."""


class QueueClosed(RuntimeError):
    """The queue has stopped accepting workloads."""


class AdmissionLeaseError(RuntimeError):
    """An admission lease could not be released cleanly."""


class RetryLimitExceeded(RuntimeError):
    """A workload exhausted its bounded admission retry policy."""


class AdmissionExpired(RuntimeError):
    """An admitted workload's lease expired before it could start."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff for denied admission."""

    max_attempts: int = 6
    base_delay: float = 0.25
    max_delay: float = 5.0
    max_wait: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not 0 < self.base_delay <= self.max_delay:
            raise ValueError("backoff delays must be positive and ordered")
        if self.max_wait <= 0:
            raise ValueError("max_wait must be positive")

    def delay_after(self, attempt: int) -> float:
        """Return the delay after a one-based denied-attempt number."""
        if attempt < 1:
            raise ValueError("attempt must be positive")
        return min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))


class AdmissionLease:
    """Opaque admission lease with idempotent asynchronous cleanup.

    ``expires_at`` uses the event loop's monotonic clock.  It is intentionally
    not included in queue status because callers must not receive host timing
    or capacity details.
    """

    def __init__(
        self,
        release: ReleaseCallback | None = None,
        *,
        expires_at: float | None = None,
    ) -> None:
        if expires_at is not None and not math.isfinite(expires_at):
            raise ValueError("expires_at must be finite")
        self._release = release or (lambda: None)
        self._expires_at = expires_at
        self._released = False
        self._release_lock = asyncio.Lock()

    @property
    def expired(self) -> bool:
        return (
            self._expires_at is not None
            and asyncio.get_running_loop().time() >= self._expires_at
        )

    @property
    def released(self) -> bool:
        return self._released

    async def release(self) -> None:
        """Release the underlying admission exactly once."""
        async with self._release_lock:
            if self._released:
                return
            self._released = True
            result = self._release()
            if inspect.isawaitable(result):
                await result


@dataclass(frozen=True, slots=True)
class QueueItemStatus:
    """Safe status projection for one workload; no host details are exposed."""

    work_id: str
    status: QueueStatus
    attempts: int
    failure_code: str | None = None
    retry_after_seconds: float | None = None


@dataclass(slots=True)
class _QueueItem(Generic[ResultT]):
    work_id: str
    work: WorkCallback[ResultT]
    task: asyncio.Task[ResultT] | None = None
    status: QueueStatus = QueueStatus.WAITING
    attempts: int = 0
    next_retry_at: float | None = None
    failure_code: str | None = None
    lease: AdmissionLease | None = None
    active_slot: bool = False
    cancel_requested: bool = False


class ScorecardJob(Generic[ResultT]):
    """Handle returned for a queued workload."""

    def __init__(self, queue: "ScorecardExecutionQueue[ResultT]", work_id: str) -> None:
        self._queue = queue
        self.work_id = work_id

    @property
    def status(self) -> QueueItemStatus:
        return self._queue.status(self.work_id)

    async def wait(self) -> ResultT:
        return await self._queue.wait(self.work_id)

    async def cancel(self) -> None:
        await self._queue.cancel(self.work_id)


class ScorecardExecutionQueue(Generic[ResultT]):
    """Bounded scorecard workload queue with admission and cleanup ownership.

    ``admit`` is the only capacity-specific seam.  It must return an
    :class:`AdmissionLease` (or ``True``) when capacity is available and
    ``None``/``False`` or :class:`CapacityDenied` while work should wait.  The
    workload callback is invoked only after that lease is acquired.
    """

    def __init__(
        self,
        *,
        max_active: int = 1,
        max_queued: int = 8,
        retry_policy: RetryPolicy | None = None,
        admit: AdmissionCallback | None = None,
    ) -> None:
        if max_active < 1:
            raise ValueError("max_active must be positive")
        if max_queued < 1:
            raise ValueError("max_queued must be positive")
        self.max_active = max_active
        self.max_queued = max_queued
        self.retry_policy = retry_policy or RetryPolicy()
        self._admit = admit or (lambda _work_id: True)
        self._items: dict[str, _QueueItem[Any]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._active = 0
        self._closed = False
        self._lock = asyncio.Lock()

    def submit(self, work_id: str, work: WorkCallback[ResultT]) -> ScorecardJob[ResultT]:
        """Enqueue a complete workload without invoking ``work`` yet."""
        if not work_id or not isinstance(work_id, str):
            raise ValueError("work_id must be a non-empty string")
        if not callable(work):
            raise TypeError("work must be callable")
        if self._closed:
            raise QueueClosed("scorecard execution queue is closed")
        if work_id in self._items:
            raise ValueError("work_id is already queued")
        nonterminal = sum(
            item.status in {QueueStatus.WAITING, QueueStatus.ADMITTED}
            for item in self._items.values()
        )
        if nonterminal >= self.max_queued:
            raise QueueFull("scorecard execution queue is full")
        item: _QueueItem[ResultT] = _QueueItem(work_id=work_id, work=work)
        task = asyncio.create_task(self._run(item), name=f"scorecard:{work_id}")
        item.task = task
        self._items[work_id] = item
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return ScorecardJob(self, work_id)

    enqueue = submit

    def status(self, work_id: str) -> QueueItemStatus:
        """Return a host-neutral status projection."""
        item = self._items.get(work_id)
        if item is None:
            raise KeyError(work_id)
        retry_after = None
        if item.status is QueueStatus.WAITING and item.next_retry_at is not None:
            retry_after = max(0.0, item.next_retry_at - asyncio.get_running_loop().time())
        return QueueItemStatus(
            work_id=item.work_id,
            status=item.status,
            attempts=item.attempts,
            failure_code=item.failure_code,
            retry_after_seconds=retry_after,
        )

    def statuses(self) -> tuple[QueueItemStatus, ...]:
        """Return all known statuses without admission or host information."""
        return tuple(self.status(work_id) for work_id in self._items)

    async def wait(self, work_id: str) -> ResultT:
        item = self._items.get(work_id)
        if item is None or item.task is None:
            raise KeyError(work_id)
        return await asyncio.shield(item.task)

    async def cancel(self, work_id: str) -> None:
        """Cancel waiting or admitted work and wait for lease cleanup."""
        item = self._items.get(work_id)
        if item is None or item.task is None:
            raise KeyError(work_id)
        if item.status in {
            QueueStatus.CANCELED,
            QueueStatus.FAILED,
            QueueStatus.COMPLETED,
        }:
            return
        item.cancel_requested = True
        item.task.cancel()
        try:
            await asyncio.shield(item.task)
        except asyncio.CancelledError:
            pass
        except BaseException:
            # The canceled task may already have failed; cleanup still ran in
            # its finally block and the public status remains authoritative.
            pass

        if item.status in {QueueStatus.WAITING, QueueStatus.ADMITTED}:
            # A task canceled before its first scheduling turn has no chance
            # to execute its coroutine-level cleanup handler.
            await self._release_item(item)
            item.status = QueueStatus.CANCELED
            item.failure_code = "canceled"

    async def aclose(self) -> None:
        """Stop intake and cancel every queued/admitted workload."""
        self._closed = True
        pending = [
            item.work_id
            for item in self._items.values()
            if item.status in {QueueStatus.WAITING, QueueStatus.ADMITTED}
        ]
        for work_id in pending:
            await self.cancel(work_id)
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def _reserve_and_admit(self, item: _QueueItem[ResultT]) -> AdmissionLease | None:
        async with self._lock:
            if self._active >= self.max_active:
                return None
            self._active += 1
            item.active_slot = True
        try:
            result = self._admit(item.work_id)
            if inspect.isawaitable(result):
                result = await result
        except CapacityDenied:
            await self._release_slot(item)
            return None
        except BaseException:
            await self._release_slot(item)
            raise
        if result is None or result is False:
            await self._release_slot(item)
            return None
        if result is True:
            lease = AdmissionLease()
        elif isinstance(result, AdmissionLease):
            lease = result
        else:
            await self._release_slot(item)
            raise TypeError("admit must return AdmissionLease, bool, or None")
        item.lease = lease
        return lease

    async def _release_slot(self, item: _QueueItem[ResultT]) -> None:
        if not item.active_slot:
            return
        async with self._lock:
            if item.active_slot:
                item.active_slot = False
                self._active -= 1

    async def _release_item(self, item: _QueueItem[ResultT]) -> BaseException | None:
        release_error: BaseException | None = None
        lease = item.lease
        item.lease = None
        if lease is not None:
            try:
                await lease.release()
            except BaseException as exc:  # cleanup must still release our slot
                release_error = exc
        await self._release_slot(item)
        return release_error

    async def _backoff(
        self, item: _QueueItem[ResultT], loop: asyncio.AbstractEventLoop, deadline: float
    ) -> None:
        if item.attempts >= self.retry_policy.max_attempts or loop.time() >= deadline:
            item.failure_code = "retry_limit"
            raise RetryLimitExceeded("scorecard admission retry limit reached")
        item.status = QueueStatus.WAITING
        delay = min(
            self.retry_policy.delay_after(item.attempts),
            max(0.0, deadline - loop.time()),
        )
        if delay <= 0:
            item.failure_code = "wait_limit"
            raise RetryLimitExceeded("scorecard admission wait limit reached")
        item.next_retry_at = loop.time() + delay
        await asyncio.sleep(delay)
        if loop.time() >= deadline:
            item.failure_code = "wait_limit"
            raise RetryLimitExceeded("scorecard admission wait limit reached")

    async def _run(self, item: _QueueItem[ResultT]) -> ResultT:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.retry_policy.max_wait
        try:
            while True:
                if item.cancel_requested:
                    raise asyncio.CancelledError
                item.attempts += 1
                item.next_retry_at = None
                lease = await self._reserve_and_admit(item)
                if lease is not None:
                    if lease.expired:
                        await self._release_item(item)
                        if item.attempts >= self.retry_policy.max_attempts:
                            item.failure_code = "admission_expired"
                            raise AdmissionExpired("scorecard admission expired")
                        await self._backoff(item, loop, deadline)
                        continue
                    item.status = QueueStatus.ADMITTED
                    try:
                        result = item.work()
                        if inspect.isawaitable(result):
                            result = await result
                    except asyncio.CancelledError:
                        raise
                    except BaseException as exc:
                        release_error = await self._release_item(item)
                        item.failure_code = "startup_error"
                        if release_error is not None:
                            raise AdmissionLeaseError("scorecard admission cleanup failed") from exc
                        raise
                    release_error = await self._release_item(item)
                    if release_error is not None:
                        item.failure_code = "lease_cleanup_error"
                        raise AdmissionLeaseError("scorecard admission cleanup failed") from release_error
                    item.status = QueueStatus.COMPLETED
                    return result  # type: ignore[return-value]

                await self._backoff(item, loop, deadline)
        except asyncio.CancelledError:
            await self._release_item(item)
            item.status = QueueStatus.CANCELED
            item.failure_code = "canceled"
            raise
        except BaseException:
            await self._release_item(item)
            item.status = QueueStatus.FAILED
            if item.failure_code is None:
                item.failure_code = "admission_error"
            raise
