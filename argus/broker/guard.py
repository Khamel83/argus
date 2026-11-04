"""Runaway-caller guard.

Deterministic rate and repeat limits keyed on the authenticated principal, with
an optional local-model review when a limit trips. Spend protection lives here,
separate from routing quality: normal callers are never rationed.

Rules (all env-configurable):

- Same normalized query repeated ``repeat_limit`` times in ``repeat_window_s``:
  further repeats run free-only (no paid provider calls).
- ``burst_limit`` calls in ``burst_window_s``: the caller runs free-only.
- ``daily_limit`` calls in 24 h: denied.

The model review can only widen the burst limit by a fixed factor for a bounded
time. It can never lift the repeat rule or the daily cap, and a failed or
unparseable review leaves the caller degraded.

State is process-local. Admission is atomic under a lock. Sharing counters
across worker processes needs a shared store and is out of scope here.
"""

from __future__ import annotations

import ipaddress
import json
import os
import threading
import time
import unicodedata
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

from argus.logging import get_logger

logger = get_logger("broker.guard")

_DAY_S = 24 * 3600
_SWEEP_INTERVAL_S = 3600
MODEL_QUERY_LIMIT = 20
MODEL_QUERY_CHARS = 200
MODEL_TIMEOUT_S = 10.0


class GuardAction(str, Enum):
    ALLOW = "allow"
    FREE_ONLY = "free_only"
    DENY = "deny"


@dataclass(frozen=True)
class GuardDecision:
    action: GuardAction
    reason: str = ""
    needs_review: bool = False
    retry_after_s: int | None = None


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class GuardConfig:
    enabled: bool = True
    repeat_limit: int = 3
    repeat_window_s: int = 600
    burst_limit: int = 60
    burst_window_s: int = 600
    daily_limit: int = 300
    degrade_s: int = 900
    exception_s: int = 3600
    exception_factor: int = 2
    model_url: str = ""
    model_name: str = ""

    @classmethod
    def from_env(cls) -> GuardConfig:
        enabled = os.environ.get("ARGUS_GUARD_ENABLED", "true").strip().lower()
        return cls(
            enabled=enabled not in {"0", "false", "no", "off"},
            repeat_limit=_env_int("ARGUS_GUARD_REPEAT_LIMIT", 3),
            burst_limit=_env_int("ARGUS_GUARD_BURST_LIMIT", 60),
            daily_limit=_env_int("ARGUS_GUARD_DAILY_LIMIT", 300),
            model_url=os.environ.get("ARGUS_GUARD_MODEL_URL", "").strip(),
            model_name=os.environ.get("ARGUS_GUARD_MODEL_NAME", "").strip(),
        )


def normalize_query(text: str) -> str:
    """NFKC, lowercase, collapse whitespace. Symbols are kept (``C++`` != ``C#``)."""
    return " ".join(unicodedata.normalize("NFKC", text).lower().split())


def validate_model_url(url: str) -> None:
    """Accept only loopback or private-network model endpoints."""
    parsed = urlparse(url)
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("ARGUS_GUARD_MODEL_URL must be an http(s) URL")
    if host == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(
            "ARGUS_GUARD_MODEL_URL must be a loopback or private IP address"
        ) from None
    if (
        address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    ):
        # 169.254.169.254 (cloud metadata) is "private" to ipaddress; refuse it.
        raise ValueError(
            "ARGUS_GUARD_MODEL_URL must not be a link-local or special address"
        )
    if not (address.is_loopback or address.is_private):
        raise ValueError("ARGUS_GUARD_MODEL_URL must not be a public address")


Reviewer = Callable[[list[dict]], Awaitable[bool]]
"""Takes recent ``{"query", "age_s"}`` entries; returns True if runaway."""


class _CallerState:
    __slots__ = ("calls", "degraded_until", "exception_until", "reviewing")

    def __init__(self) -> None:
        self.calls: deque[tuple[float, str, str]] = deque()  # (ts, hash, text)
        self.degraded_until = 0.0
        self.exception_until = 0.0
        self.reviewing = False


class RunawayGuard:
    def __init__(
        self,
        config: GuardConfig | None = None,
        *,
        reviewer: Reviewer | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config or GuardConfig.from_env()
        self._clock = clock
        self._lock = threading.Lock()
        self._callers: dict[str, _CallerState] = {}
        self._last_sweep = 0.0
        self._reviewer = reviewer
        if reviewer is None and self._config.model_url:
            validate_model_url(self._config.model_url)
            self._reviewer = _http_reviewer(
                self._config.model_url, self._config.model_name
            )

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def admit(self, principal: str, query_text: str) -> GuardDecision:
        """Atomically check limits and reserve one call for ``principal``."""
        cfg = self._config
        if not cfg.enabled:
            return GuardDecision(GuardAction.ALLOW)
        now = self._clock()
        key = hash(normalize_query(query_text))
        with self._lock:
            self._sweep(now)
            state = self._callers.setdefault(principal, _CallerState())
            while state.calls and state.calls[0][0] < now - _DAY_S:
                state.calls.popleft()
            if len(state.calls) >= cfg.daily_limit:
                # Rolling window: admission is possible once the oldest counted
                # call is a day old.
                retry = max(1, int(state.calls[0][0] + _DAY_S - now) + 1)
                return GuardDecision(
                    GuardAction.DENY, "daily call limit reached", retry_after_s=retry
                )
            state.calls.append(
                (now, str(key), normalize_query(query_text)[:MODEL_QUERY_CHARS])
            )

            repeats = sum(
                1
                for ts, h, _ in state.calls
                if h == str(key) and ts >= now - cfg.repeat_window_s
            )
            burst = sum(1 for ts, _, _ in state.calls if ts >= now - cfg.burst_window_s)
            burst_limit = cfg.burst_limit * (
                cfg.exception_factor if now < state.exception_until else 1
            )

            if repeats > cfg.repeat_limit:
                return GuardDecision(
                    GuardAction.FREE_ONLY, "repeated query", needs_review=False
                )
            if burst > burst_limit:
                state.degraded_until = max(state.degraded_until, now + cfg.degrade_s)
                review = self._reviewer is not None and not state.reviewing
                if review:
                    state.reviewing = True
                return GuardDecision(
                    GuardAction.FREE_ONLY, "call burst", needs_review=review
                )
            if now < state.degraded_until:
                return GuardDecision(GuardAction.FREE_ONLY, "caller degraded")
            return GuardDecision(GuardAction.ALLOW)

    def _sweep(self, now: float) -> None:
        """Drop principals with no calls in the last day (caller holds the lock)."""
        if now - self._last_sweep < _SWEEP_INTERVAL_S:
            return
        self._last_sweep = now
        for principal in [
            name
            for name, state in self._callers.items()
            if not state.reviewing
            and (not state.calls or state.calls[-1][0] < now - _DAY_S)
            and state.exception_until < now
        ]:
            del self._callers[principal]

    async def review(self, principal: str) -> None:
        """Ask the model whether the recent calls look like a loop.

        Fails closed: any error, timeout or unparseable reply keeps the caller
        degraded. A "legitimate" verdict widens only the burst limit.
        """
        import asyncio

        if self._reviewer is None:
            return
        with self._lock:
            state = self._callers.get(principal)
            if state is None:
                return
            now = self._clock()
            recent = [
                {"query": text, "age_s": int(now - ts)}
                for ts, _, text in list(state.calls)[-MODEL_QUERY_LIMIT:]
            ]
        runaway = True
        try:
            runaway = await asyncio.wait_for(
                self._reviewer(recent), timeout=MODEL_TIMEOUT_S
            )
        except Exception as exc:  # noqa: BLE001 - any failure must fail closed
            logger.warning("guard review failed (%s); keeping caller degraded", exc)
        finally:
            with self._lock:
                state = self._callers.get(principal)
                if state is not None:
                    state.reviewing = False
                    if not runaway:
                        state.exception_until = self._clock() + self._config.exception_s
                        state.degraded_until = 0.0
        logger.info("guard review verdict principal=%s runaway=%s", principal, runaway)


def _http_reviewer(url: str, model: str = "") -> Reviewer:
    """OpenAI-compatible chat endpoint (Ollama, llama.cpp, LM Studio)."""

    async def review(recent: list[dict]) -> bool:
        import httpx

        prompt = (
            "These are the most recent search queries from one API caller. "
            'Reply with JSON only: {"runaway": true|false, "reason": "..."}. '
            "runaway=true if they look like an automated loop or a stuck "
            "process; false if they look like normal varied work.\n"
            + json.dumps(recent)
        )
        # trust_env=False: never route query text through an env-configured proxy.
        async with httpx.AsyncClient(
            timeout=MODEL_TIMEOUT_S, trust_env=False
        ) as client:
            response = await client.post(
                url,
                json={
                    # OpenAI-compatible servers require "model" (Ollama,
                    # LM Studio); some single-model servers ignore it.
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        verdict = json.loads(content[content.index("{") : content.rindex("}") + 1])
        runaway = verdict["runaway"]
        if not isinstance(runaway, bool):
            raise TypeError("runaway must be boolean")
        return runaway

    return review
