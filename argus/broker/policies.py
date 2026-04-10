"""
Mode-based routing policies with tier-aware credit ordering.

Each search mode defines a preferred provider priority list.
Providers are then sorted by tier (credit-aware) as the primary key,
preserving mode-specific ordering within each tier.

Tier 0: Free/unlimited (SearXNG) — always first
Tier 1: Monthly recurring credits (Brave, Tavily, Linkup, Exa)
Tier 2: Semi-monthly credits
Tier 3: One-time credits (Serper, Parallel, You.com, SearchAPI) — always last
"""

from argus.broker.budgets import PROVIDER_TIERS
from argus.models import ProviderName, SearchMode

# Mode-specific provider preference lists.
# These define query-type routing (which providers are best for each mode).
# The final routing order is sorted by tier, preserving mode order within each tier.
MODE_PROVIDER_PREFERENCES: dict[SearchMode, list[ProviderName]] = {
    SearchMode.RECOVERY: [
        ProviderName.SEARXNG,
        ProviderName.DUCKDUCKGO,
        ProviderName.BRAVE,
        ProviderName.SERPER,
        ProviderName.TAVILY,
        ProviderName.EXA,
        ProviderName.LINKUP,
        ProviderName.PARALLEL,
        ProviderName.YOU,
    ],
    SearchMode.DISCOVERY: [
        ProviderName.SEARXNG,
        ProviderName.DUCKDUCKGO,
        ProviderName.BRAVE,
        ProviderName.EXA,
        ProviderName.TAVILY,
        ProviderName.LINKUP,
        ProviderName.SERPER,
        ProviderName.PARALLEL,
        ProviderName.YOU,
    ],
    SearchMode.GROUNDING: [
        ProviderName.SEARXNG,
        ProviderName.DUCKDUCKGO,
        ProviderName.BRAVE,
        ProviderName.SERPER,
        ProviderName.LINKUP,
        ProviderName.PARALLEL,
        ProviderName.YOU,
    ],
    SearchMode.RESEARCH: [
        ProviderName.SEARXNG,
        ProviderName.DUCKDUCKGO,
        ProviderName.TAVILY,
        ProviderName.EXA,
        ProviderName.BRAVE,
        ProviderName.LINKUP,
        ProviderName.SERPER,
        ProviderName.PARALLEL,
        ProviderName.YOU,
    ],
}


def get_provider_order(mode: SearchMode) -> list[ProviderName]:
    """Return tier-sorted provider list for a given search mode.

    CACHE is always prepended. Remaining providers are sorted by tier
    (stable sort preserves mode-specific ordering within each tier).
    """
    preferences = MODE_PROVIDER_PREFERENCES.get(
        mode, MODE_PROVIDER_PREFERENCES[SearchMode.DISCOVERY]
    )
    # Stable sort by tier: free first, monthly next, one-time last
    tier_sorted = sorted(preferences, key=lambda p: PROVIDER_TIERS.get(p, 99))
    return [ProviderName.CACHE, *tier_sorted]


def resolve_routing(
    mode: SearchMode,
    override_providers: list[ProviderName] | None,
) -> list[ProviderName]:
    """Resolve the final provider routing order.

    If caller overrides providers, sort those by tier too.
    Otherwise use the mode-based tier-sorted policy.
    """
    if override_providers:
        tier_sorted = sorted(override_providers, key=lambda p: PROVIDER_TIERS.get(p, 99))
        return tier_sorted
    return get_provider_order(mode)
