"""Bounded process-local MCP transport session ownership."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace


MCP_SESSION_IDLE_TIMEOUT_SECONDS = 30 * 60
MCP_MAX_ACTIVE_SESSIONS = 256
MCP_SESSION_SWEEP_LIMIT = 64


class McpSessionCapacityError(RuntimeError):
    """The fixed active MCP transport-session capacity is exhausted."""


@dataclass(frozen=True, slots=True)
class McpSession:
    session_id: str
    principal: str
    protocol_version: str
    created_at: float
    last_used_at: float


class McpSessionRegistry:
    """Atomic principal-owned registry for process-local transport sessions."""

    def __init__(
        self,
        *,
        max_active: int = MCP_MAX_ACTIVE_SESSIONS,
        idle_timeout_seconds: float = MCP_SESSION_IDLE_TIMEOUT_SECONDS,
        sweep_limit: int = MCP_SESSION_SWEEP_LIMIT,
        clock: Callable[[], float] = time.monotonic,
        id_factory: Callable[[], str] | None = None,
        on_remove: Callable[[McpSession], None] | None = None,
    ):
        if max_active < 1:
            raise ValueError("max_active must be positive")
        if idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be positive")
        if sweep_limit < 1:
            raise ValueError("sweep_limit must be positive")
        self._max_active = max_active
        self._idle_timeout_seconds = idle_timeout_seconds
        self._sweep_limit = sweep_limit
        self._clock = clock
        self._id_factory = id_factory or (lambda: secrets.token_urlsafe(32))
        self._on_remove = on_remove
        self._sessions: dict[str, McpSession] = {}
        self._lock = threading.Lock()

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def bind_removal_callback(
        self,
        callback: Callable[[McpSession], None],
    ) -> None:
        """Bind the transport cleanup callback before listener startup."""
        with self._lock:
            if self._on_remove is not None:
                raise RuntimeError("MCP session removal callback is already bound")
            self._on_remove = callback

    def initialize(self, principal: str, protocol_version: str) -> McpSession:
        """Reclaim expiry, reserve capacity, collision-check, and insert atomically."""
        now = self._clock()
        with self._lock:
            removed = self._reclaim_expired_locked(now, limit=None)
            if len(self._sessions) >= self._max_active:
                session = None
            else:
                session_id = self._new_unique_id_locked()
                session = McpSession(
                    session_id=session_id,
                    principal=principal,
                    protocol_version=protocol_version,
                    created_at=now,
                    last_used_at=now,
                )
                self._sessions[session_id] = session
        self._notify_removed(removed)
        if session is None:
            raise McpSessionCapacityError("MCP transport session capacity exhausted")
        return session

    def lookup(self, session_id: str, principal: str) -> McpSession | None:
        now = self._clock()
        with self._lock:
            removed = self._reclaim_expired_locked(now, limit=self._sweep_limit)
            session = self._sessions.get(session_id)
            if session is not None and self._expired(session, now):
                removed.append(self._sessions.pop(session_id))
                session = None
            if session is not None and session.principal != principal:
                session = None
        self._notify_removed(removed)
        return session

    def touch(self, session_id: str, principal: str) -> McpSession | None:
        now = self._clock()
        with self._lock:
            removed = self._reclaim_expired_locked(now, limit=self._sweep_limit)
            session = self._sessions.get(session_id)
            if session is not None and self._expired(session, now):
                removed.append(self._sessions.pop(session_id))
                session = None
            if session is None or session.principal != principal:
                touched = None
            else:
                touched = replace(session, last_used_at=now)
                self._sessions[session_id] = touched
        self._notify_removed(removed)
        return touched

    def terminate(self, session_id: str, principal: str) -> bool:
        now = self._clock()
        with self._lock:
            removed = self._reclaim_expired_locked(now, limit=self._sweep_limit)
            session = self._sessions.get(session_id)
            if session is not None and self._expired(session, now):
                removed.append(self._sessions.pop(session_id))
                session = None
            if session is None or session.principal != principal:
                terminated = False
            else:
                removed.append(self._sessions.pop(session_id))
                terminated = True
        self._notify_removed(removed)
        return terminated

    def sweep(self) -> int:
        """Remove at most the configured bounded number of expired sessions."""
        with self._lock:
            removed = self._reclaim_expired_locked(
                self._clock(),
                limit=self._sweep_limit,
            )
        self._notify_removed(removed)
        return len(removed)

    def _new_unique_id_locked(self) -> str:
        for _ in range(128):
            candidate = self._id_factory()
            if (
                candidate
                and len(candidate) <= 128
                and candidate.isascii()
                and candidate.isprintable()
                and candidate not in self._sessions
            ):
                return candidate
        raise RuntimeError("unable to allocate a unique MCP transport session ID")

    def _expired(self, session: McpSession, now: float) -> bool:
        return now - session.last_used_at >= self._idle_timeout_seconds

    def _reclaim_expired_locked(
        self,
        now: float,
        *,
        limit: int | None,
    ) -> list[McpSession]:
        removed = []
        for session_id, session in tuple(self._sessions.items()):
            if self._expired(session, now):
                removed.append(self._sessions.pop(session_id))
                if limit is not None and len(removed) >= limit:
                    break
        return removed

    def _notify_removed(self, sessions: list[McpSession]) -> None:
        if self._on_remove is None:
            return
        for session in sessions:
            self._on_remove(session)
