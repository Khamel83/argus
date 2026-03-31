"""
Provider health tracking.

Tracks last success/failure, consecutive failures, cooldown windows.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from argus.models import ProviderName, ProviderStatus


@dataclass
class ProviderHealth:
    provider: ProviderName
    consecutive_failures: int = 0
    last_success: Optional[float] = None  # timestamp
    last_failure: Optional[float] = None  # timestamp
    disabled_until: Optional[float] = None  # cooldown deadline

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.last_success = time.time()

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.last_failure = time.time()

    def is_in_cooldown(self) -> bool:
        if self.disabled_until is None:
            return False
        return time.time() < self.disabled_until

    def apply_cooldown(self, minutes: int) -> None:
        self.disabled_until = time.time() + (minutes * 60)


class HealthTracker:
    def __init__(self, failure_threshold: int = 5, cooldown_minutes: int = 60):
        self._failure_threshold = failure_threshold
        self._cooldown_minutes = cooldown_minutes
        self._health: dict[ProviderName, ProviderHealth] = {}

    def _get(self, provider: ProviderName) -> ProviderHealth:
        if provider not in self._health:
            self._health[provider] = ProviderHealth(provider=provider)
        return self._health[provider]

    def record_success(self, provider: ProviderName) -> None:
        self._get(provider).record_success()

    def record_failure(self, provider: ProviderName) -> None:
        health = self._get(provider)
        health.record_failure()
        if health.consecutive_failures >= self._failure_threshold:
            health.apply_cooldown(self._cooldown_minutes)

    def get_status(self, provider: ProviderName) -> Optional[ProviderStatus]:
        health = self._get(provider)
        if health.is_in_cooldown():
            return ProviderStatus.TEMPORARILY_DISABLED
        if health.consecutive_failures >= self._failure_threshold:
            return ProviderStatus.DEGRADED
        return None

    def get_health(self, provider: ProviderName) -> ProviderHealth:
        return self._get(provider)

    def get_all_status(self) -> dict[ProviderName, dict]:
        result = {}
        for provider, health in self._health.items():
            status = self.get_status(provider)
            result[provider] = {
                "consecutive_failures": health.consecutive_failures,
                "last_success": health.last_success,
                "last_failure": health.last_failure,
                "in_cooldown": health.is_in_cooldown(),
                "status_override": status.value if status else None,
            }
        return result
