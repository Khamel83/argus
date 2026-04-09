"""
Provider budget/credit tracking.

Tracks query counts per provider on a monthly rolling window.
Budgets are expressed in query counts (not USD) — set the monthly limit
and each successful provider call costs 1.0 queries.
Supports optional SQLite persistence via BudgetStore.
"""

import os
import time
from collections import defaultdict
from typing import Optional

from argus.logging import get_logger
from argus.models import ProviderName, ProviderStatus

logger = get_logger("broker.budgets")

# Provider tiers — used by routing to prioritize recurring credits over one-time.
# Tier 0: SearXNG (unlimited, always available)
# Tier 1: Monthly recurring credits (refresh every month)
# Tier 2: Monthly or semi-monthly credits
# Tier 3: One-time signup credits (don't come back)
PROVIDER_TIERS: dict[ProviderName, int] = {
    ProviderName.SEARXNG: 0,  # free, unlimited
    ProviderName.BRAVE: 1,     # monthly recurring
    ProviderName.TAVILY: 1,    # monthly recurring
    ProviderName.LINKUP: 1,    # monthly recurring
    ProviderName.EXA: 1,       # monthly (likely)
    ProviderName.SERPER: 3,    # one-time credits
    ProviderName.PARALLEL: 3,  # one-time credits
    ProviderName.YOU: 3,       # one-time credits
    ProviderName.SEARCHAPI: 3,  # one-time/placeholder
}


class BudgetTracker:
    def __init__(self, persist_path: Optional[str] = None):
        # provider -> list of (timestamp, cost)
        # cost is 1.0 per query for all providers
        self._usage: dict[ProviderName, list[tuple[float, float]]] = defaultdict(list)
        # provider -> monthly budget limit (in query count)
        self._budgets: dict[ProviderName, float] = {}
        self._store = None

        if persist_path:
            from argus.broker.budget_persistence import BudgetStore
            self._store = BudgetStore(persist_path)
            self._load_from_store()

    def _load_from_store(self) -> None:
        """Load historical usage from SQLite into memory for fast checks."""
        from argus.models import ProviderName as PN

        cutoff = time.time() - (30 * 24 * 3600)
        try:
            conn = self._store._get_conn()
            rows = conn.execute(
                "SELECT provider, timestamp, cost_usd FROM budget_usage "
                "WHERE timestamp >= ?",
                (cutoff,),
            ).fetchall()
            for provider_str, ts, cost in rows:
                try:
                    pname = PN(provider_str)
                    self._usage[pname].append((ts, cost))
                except ValueError:
                    pass
            logger.info("Loaded %d budget records from store", len(rows))
        except Exception as e:
            logger.warning("Failed to load budget history: %s", e)

    def set_budget(self, provider: ProviderName, monthly_budget: float) -> None:
        """Set monthly query limit for a provider (0 = unlimited)."""
        self._budgets[provider] = monthly_budget

    def record_usage(self, provider: ProviderName, cost: float = 1.0) -> None:
        """Record one query against a provider's budget."""
        self._usage[provider].append((time.time(), cost))
        if self._store:
            self._store.record_usage(provider.value, cost)

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
        """Return number of queries used this month for a provider."""
        cutoff = time.time() - (30 * 24 * 3600)
        return len([1 for t, _ in self._usage[provider] if t >= cutoff])

    def get_provider_tier(self, provider: ProviderName) -> int:
        """Return the provider's tier (0=free, 1=monthly, 2=semi-monthly, 3=one-time)."""
        return PROVIDER_TIERS.get(provider, 99)

    def check_status(self, provider: ProviderName) -> Optional[ProviderStatus]:
        if self.is_budget_exhausted(provider):
            return ProviderStatus.BUDGET_EXHAUSTED
        return None

    def close(self) -> None:
        if self._store:
            self._store.close()
