"""
Provider budget/credit tracking.

Tracks query counts per provider. Budgets are expressed in query counts
(not USD) — set the limit and each successful provider call costs 1.0 queries.

Tier 0 (free): no tracking needed — always queried.
Tier 1 (monthly recurring): 30-day rolling window. Credits refresh monthly.
Tier 3 (one-time): lifetime counter. Credits never replenish.

Supports optional SQLite persistence via BudgetStore.
"""

import time
from collections import defaultdict
from typing import Optional

from argus.logging import get_logger
from argus.models import ProviderName, ProviderStatus

logger = get_logger("broker.budgets")

# Provider tiers — used by routing to prioritize recurring credits over one-time.
# Tier 0: Free/unlimited (SearXNG, DuckDuckGo, Yahoo, GitHub, Wolfram)
# Tier 1: Monthly recurring credits (Brave, Tavily, Exa, Linkup)
# Tier 3: One-time signup credits (Serper, Parallel, You, SearchAPI, Valyu)
PROVIDER_TIERS: dict[ProviderName, int] = {
    ProviderName.SEARXNG: 0,     # free, unlimited (self-hosted, 70+ engines)
    ProviderName.DUCKDUCKGO: 0,  # free, unlimited (scrapes DDG)
    ProviderName.YAHOO: 0,       # free, unlimited (scraped, fragile)
    ProviderName.GITHUB: 0,      # free, unlimited (rate-limited but no cost)
    ProviderName.WOLFRAM: 0,     # free, 2k/month (API key required)
    ProviderName.BRAVE: 1,       # monthly recurring
    ProviderName.TAVILY: 1,      # monthly recurring
    ProviderName.LINKUP: 1,      # monthly recurring
    ProviderName.EXA: 1,         # monthly recurring
    ProviderName.SERPER: 3,      # one-time credits
    ProviderName.PARALLEL: 3,    # one-time credits
    ProviderName.YOU: 3,         # one-time credits
    ProviderName.SEARCHAPI: 3,   # one-time/placeholder
    ProviderName.VALYU: 3,       # one-time credits ($10)
}

# Tier 3 providers use lifetime counters (never reset) instead of rolling windows.
_TIER_3_PROVIDERS = {p for p, t in PROVIDER_TIERS.items() if t == 3}


class BudgetTracker:
    def __init__(self, persist_path: Optional[str] = None):
        # provider -> list of (timestamp, cost)
        # cost is 1.0 per query for all providers
        self._usage: dict[ProviderName, list[tuple[float, float]]] = defaultdict(list)
        # provider -> budget limit (in query count)
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
            # Also load ALL records for tier-3 (lifetime tracking, no cutoff)
            if _TIER_3_PROVIDERS:
                placeholders = ",".join("?" for _ in _TIER_3_PROVIDERS)
                tier3_names = [p.value for p in _TIER_3_PROVIDERS]
                tier3_rows = conn.execute(
                    f"SELECT provider, timestamp, cost_usd FROM budget_usage "
                    f"WHERE provider IN ({placeholders})",
                    tier3_names,
                ).fetchall()
            else:
                tier3_rows = []

            seen: set[tuple[str, float, float]] = set()
            for provider_str, ts, cost in rows + tier3_rows:
                key = (provider_str, ts, cost)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    pname = PN(provider_str)
                    self._usage[pname].append((ts, cost))
                except ValueError:
                    pass
            logger.info("Loaded %d budget records from store", len(seen))
        except Exception as e:
            logger.warning("Failed to load budget history: %s", e)

    def set_budget(self, provider: ProviderName, budget: float) -> None:
        """Set query limit for a provider (0 = unlimited).

        For tier 1 (monthly), this is a rolling 30-day cap.
        For tier 3 (one-time), this is a lifetime cap.
        """
        self._budgets[provider] = budget

    def get_budget_limit(self, provider: ProviderName) -> float:
        """Return the configured cap, or zero when the provider is uncapped."""
        return self._budgets.get(provider, 0.0)

    def record_usage(self, provider: ProviderName, cost: float = 1.0) -> None:
        """Record one query against a provider's budget."""
        self._usage[provider].append((time.time(), cost))
        if self._store:
            self._store.record_usage(provider.value, cost)

    def get_monthly_usage(self, provider: ProviderName) -> float:
        """Return total usage. Lifetime for tier-3, rolling 30-day for tier-1."""
        if provider in _TIER_3_PROVIDERS:
            return sum(c for _, c in self._usage[provider])
        cutoff = time.time() - (30 * 24 * 3600)
        return sum(c for t, c in self._usage[provider] if t >= cutoff)

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

    def used_today(self, provider: ProviderName) -> int:
        """Return number of queries used in the last 24 hours."""
        cutoff = time.time() - (24 * 3600)
        return len([1 for t, _ in self._usage[provider] if t >= cutoff])

    def daily_pace(self, provider: ProviderName) -> float:
        """Credits per day the remaining budget allows.

        Tier 0/1 (free/monthly): remaining / 30.
        Tier 3 (one-time): infinity while credits remain (no pacing — exhaustion is the sole gate).
        """
        remaining = self.get_remaining_budget(provider)
        if remaining is None:
            return float("inf")
        if provider in _TIER_3_PROVIDERS:
            return float("inf") if remaining > 0 else 0.0
        return remaining / 30.0

    def is_over_pace(self, provider: ProviderName) -> bool:
        """True if sustained usage would exhaust the budget within 7 days.

        For tier 3 (one-time), always False — exhaustion is the only gate.
        For tier 1 (monthly), checks if the 7-day usage rate would drain
        the remaining budget in under a week. Empty days bank headroom.
        """
        remaining = self.get_remaining_budget(provider)
        if remaining is None or remaining <= 0:
            return remaining is not None  # True only if budget exists and is 0

        if provider in _TIER_3_PROVIDERS:
            return False

        # 7-day usage rate: how many queries per day over the last week
        week_cutoff = time.time() - (7 * 24 * 3600)
        week_usage = sum(c for t, c in self._usage[provider] if t >= week_cutoff)
        if week_usage == 0:
            return False

        daily_rate = week_usage / 7.0
        days_until_exhausted = remaining / daily_rate
        return days_until_exhausted < 7

    def get_usage_count(self, provider: ProviderName) -> int:
        """Return total queries used. Lifetime for tier-3, rolling 30-day for tier-1."""
        if provider in _TIER_3_PROVIDERS:
            return len(self._usage[provider])
        cutoff = time.time() - (30 * 24 * 3600)
        return len([1 for t, _ in self._usage[provider] if t >= cutoff])

    def get_provider_tier(self, provider: ProviderName) -> int:
        return PROVIDER_TIERS.get(provider, 99)

    def reset_lifetime_usage(self, provider: ProviderName) -> int:
        """Reset all usage for a tier-3 provider (e.g., after topping up credits).

        Returns count of cleared records.
        """
        count = len(self._usage[provider])
        self._usage[provider] = []
        if self._store and provider in _TIER_3_PROVIDERS:
            self._store.delete_provider_usage(provider.value)
        return count

    def check_status(self, provider: ProviderName) -> Optional[ProviderStatus]:
        if self.is_budget_exhausted(provider):
            return ProviderStatus.BUDGET_EXHAUSTED
        return None

    def close(self) -> None:
        if self._store:
            self._store.close()
