"""
Provider budget tracking.

Tracks usage counts, estimated spend, and monthly totals per provider.
In-memory state — would be persisted in production.
"""

import time
from collections import defaultdict
from typing import Optional

from argus.models import ProviderName, ProviderStatus


class BudgetTracker:
    def __init__(self):
        # provider -> list of (timestamp, cost_estimate_usd)
        self._usage: dict[ProviderName, list[tuple[float, float]]] = defaultdict(list)
        # provider -> monthly budget limit
        self._budgets: dict[ProviderName, float] = {}

    def set_budget(self, provider: ProviderName, monthly_budget_usd: float) -> None:
        self._budgets[provider] = monthly_budget_usd

    def record_usage(self, provider: ProviderName, cost_estimate_usd: float = 0.0) -> None:
        self._usage[provider].append((time.time(), cost_estimate_usd))

    def get_monthly_usage(self, provider: ProviderName) -> float:
        cutoff = time.time() - (30 * 24 * 3600)
        entries = [c for t, c in self._usage[provider] if t >= cutoff]
        return sum(entries)

    def get_remaining_budget(self, provider: ProviderName) -> Optional[float]:
        budget = self._budgets.get(provider)
        if budget is None or budget <= 0:
            return None  # no budget set = unlimited
        return max(0.0, budget - self.get_monthly_usage(provider))

    def is_budget_exhausted(self, provider: ProviderName) -> bool:
        remaining = self.get_remaining_budget(provider)
        if remaining is None:
            return False
        return remaining <= 0

    def get_usage_count(self, provider: ProviderName) -> int:
        cutoff = time.time() - (30 * 24 * 3600)
        return len([1 for t, _ in self._usage[provider] if t >= cutoff])

    def check_status(self, provider: ProviderName) -> Optional[ProviderStatus]:
        if self.is_budget_exhausted(provider):
            return ProviderStatus.BUDGET_EXHAUSTED
        return None
