"""Lifecycle-owned execution for bounded synchronous background operations."""

from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Generic, TypeVar


ResultT = TypeVar("ResultT")


class LifecycleCapability(str, Enum):
    """Whether synchronous work is safe to own during application shutdown."""

    COOPERATIVE_BOUNDED = "cooperative_bounded"
    BLOCKING_UNSAFE = "blocking_unsafe"


class UnsafeLifecycleOperation(RuntimeError):
    """Raised before an operation without a bounded shutdown contract starts."""


@dataclass(frozen=True)
class LifecycleOperation(Generic[ResultT]):
    name: str
    capability: LifecycleCapability
    call: Callable[[threading.Event], ResultT]


@dataclass(frozen=True)
class _WorkItem(Generic[ResultT]):
    operation: LifecycleOperation[ResultT]
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[ResultT]


class LifecycleWorker:
    """One joinable worker owned by a single FastAPI lifespan.

    Accepted operations must enforce finite I/O deadlines and observe
    ``stop_event`` between bounded units of work. Shutdown first signals that
    event, rejects queued work, joins the worker, and only then permits resource
    cleanup.
    """

    def __init__(self, *, name: str = "argus-lifecycle") -> None:
        self.stop_event = threading.Event()
        self._queue: queue.Queue[_WorkItem | None] = queue.Queue()
        self._state_lock = threading.Lock()
        self._stopping = False
        self._thread = threading.Thread(
            target=self._run,
            name=name,
            daemon=False,
        )
        self._thread.start()

    async def run(self, operation: LifecycleOperation[ResultT]) -> ResultT:
        if operation.capability is not LifecycleCapability.COOPERATIVE_BOUNDED:
            raise UnsafeLifecycleOperation(
                f"{operation.name} lacks a bounded lifecycle contract"
            )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ResultT] = loop.create_future()
        with self._state_lock:
            if self._stopping:
                raise asyncio.CancelledError
            self._queue.put(_WorkItem(operation, loop, future))
        return await future

    def request_stop(self) -> None:
        with self._state_lock:
            if self._stopping:
                return
            self._stopping = True
            self.stop_event.set()
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is not None:
                    item.loop.call_soon_threadsafe(self._cancel, item.future)
            self._queue.put(None)

    async def aclose(self) -> None:
        self.request_stop()
        while self._thread.is_alive():
            await asyncio.sleep(0.005)
        self._thread.join()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            if self.stop_event.is_set():
                item.loop.call_soon_threadsafe(self._cancel, item.future)
                continue
            try:
                result = item.operation.call(self.stop_event)
            except BaseException as exc:
                item.loop.call_soon_threadsafe(self._set_exception, item.future, exc)
            else:
                item.loop.call_soon_threadsafe(self._set_result, item.future, result)

    @staticmethod
    def _cancel(future: asyncio.Future) -> None:
        if not future.done():
            future.cancel()

    @staticmethod
    def _set_result(future: asyncio.Future, result: object) -> None:
        if not future.done():
            future.set_result(result)

    @staticmethod
    def _set_exception(future: asyncio.Future, exc: BaseException) -> None:
        if not future.done():
            future.set_exception(exc)
