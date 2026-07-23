"""
Provider health tracking.

Tracks last success/failure, consecutive failures, cooldown windows.
"""

import time
from dataclasses import dataclass
import threading
from typing import Optional

from argus.models import ProviderName, ProviderStatus


@dataclass
class ProviderHealth:
    provider: ProviderName
    consecutive_failures: int = 0
    last_success: Optional[float] = None  # timestamp
    last_failure: Optional[float] = None  # timestamp
    disabled_until: Optional[float] = None  # cooldown deadline
    half_open_claimed: bool = False

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.last_success = time.time()
        self.disabled_until = None
        self.half_open_claimed = False

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.last_failure = time.time()

    def is_in_cooldown(self) -> bool:
        if self.disabled_until is None:
            return False
        return time.time() < self.disabled_until

    def apply_cooldown(self, minutes: int) -> None:
        self.disabled_until = time.time() + (minutes * 60)
        self.half_open_claimed = False


@dataclass(frozen=True)
class ProviderHealthSnapshot:
    provider: ProviderName
    consecutive_failures: int
    last_success: Optional[float]
    last_failure: Optional[float]
    disabled_until: Optional[float]
    half_open_claimed: bool

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "consecutive_failures": self.consecutive_failures,
            "last_success": self.last_success,
            "last_failure": self.last_failure,
            "disabled_until": self.disabled_until,
            "half_open_claimed": self.half_open_claimed,
        }


@dataclass(frozen=True)
class ProviderHealthEvidence:
    health: ProviderHealthSnapshot | None
    status_override: ProviderStatus | None


@dataclass(frozen=True)
class HealthExecutionClaim:
    provider: ProviderName
    half_open: bool


class HealthTracker:
    def __init__(self, failure_threshold: int = 5, cooldown_minutes: int = 60):
        self._failure_threshold = failure_threshold
        self._cooldown_minutes = cooldown_minutes
        self._health: dict[ProviderName, ProviderHealth] = {}
        self._lock = threading.RLock()

    def _get(self, provider: ProviderName) -> ProviderHealth:
        with self._lock:
            if provider not in self._health:
                self._health[provider] = ProviderHealth(provider=provider)
            return self._health[provider]

    def record_success(self, provider: ProviderName) -> None:
        with self._lock:
            self._get(provider).record_success()

    def record_failure(self, provider: ProviderName) -> None:
        with self._lock:
            health = self._get(provider)
            health.record_failure()
            if health.consecutive_failures >= self._failure_threshold:
                health.apply_cooldown(self._cooldown_minutes)

    def get_status(self, provider: ProviderName) -> Optional[ProviderStatus]:
        return self._status(provider, claim_half_open=True)

    def peek_status(self, provider: ProviderName) -> Optional[ProviderStatus]:
        """Read status without consuming a post-cooldown half-open attempt."""
        return self._status(provider, claim_half_open=False)

    def peek_execution_status(self, provider: ProviderName) -> Optional[ProviderStatus]:
        """Plan an invocation without consuming an available half-open token."""
        with self._lock:
            health = self._health.get(provider)
            if (
                health is not None
                and not health.is_in_cooldown()
                and health.consecutive_failures >= self._failure_threshold
                and health.disabled_until is not None
                and not health.half_open_claimed
            ):
                return None
            return self.peek_status(provider)

    def claim_execution(self, provider: ProviderName) -> HealthExecutionClaim | None:
        """Atomically claim provider execution immediately before invocation."""
        with self._lock:
            health = self._health.get(provider)
            if health is None or health.consecutive_failures < self._failure_threshold:
                return HealthExecutionClaim(provider=provider, half_open=False)
            if health.is_in_cooldown() or health.half_open_claimed:
                return None
            if health.disabled_until is not None:
                health.half_open_claimed = True
                return HealthExecutionClaim(provider=provider, half_open=True)
            return None

    def release_execution_claim(self, claim: HealthExecutionClaim) -> None:
        if not claim.half_open:
            return
        with self._lock:
            health = self._health.get(claim.provider)
            if health is not None:
                health.half_open_claimed = False

    def _status(
        self,
        provider: ProviderName,
        *,
        claim_half_open: bool,
    ) -> Optional[ProviderStatus]:
        with self._lock:
            health = self._health.get(provider)
            if health is None:
                return None
            if health.is_in_cooldown():
                return ProviderStatus.TEMPORARILY_DISABLED
            if health.consecutive_failures >= self._failure_threshold:
                if (
                    claim_half_open
                    and health.disabled_until is not None
                    and not health.half_open_claimed
                ):
                    health.half_open_claimed = True
                    return None
                return ProviderStatus.DEGRADED
            return None

    def peek_health(self, provider: ProviderName) -> ProviderHealth | None:
        """Return observed process-local evidence without creating a record."""
        with self._lock:
            return self._health.get(provider)

    def snapshot(self, provider: ProviderName) -> ProviderHealthSnapshot | None:
        with self._lock:
            health = self._health.get(provider)
            if health is None:
                return None
            return ProviderHealthSnapshot(
                provider=health.provider,
                consecutive_failures=health.consecutive_failures,
                last_success=health.last_success,
                last_failure=health.last_failure,
                disabled_until=health.disabled_until,
                half_open_claimed=health.half_open_claimed,
            )

    def evidence_snapshot(self, provider: ProviderName) -> ProviderHealthEvidence:
        """Atomically copy health fields and their derived status."""
        with self._lock:
            return ProviderHealthEvidence(
                health=self.snapshot(provider),
                status_override=self.peek_status(provider),
            )

    def get_health(self, provider: ProviderName) -> ProviderHealth:
        return self._get(provider)

    def get_all_status(self) -> dict[ProviderName, dict]:
        with self._lock:
            result = {}
            for provider, health in self._health.items():
                status = self.peek_status(provider)
                result[provider] = {
                    "consecutive_failures": health.consecutive_failures,
                    "last_success": health.last_success,
                    "last_failure": health.last_failure,
                    "in_cooldown": health.is_in_cooldown(),
                    "status_override": status.value if status else None,
                }
            return result
