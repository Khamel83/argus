"""
Mode-based routing policies.

Each search mode defines an ordered provider priority list.
The broker routes queries through these in order.
"""

from argus.models import ProviderName, SearchMode

# Policy: mode -> ordered list of providers (first = highest priority)
ROUTING_POLICIES: dict[SearchMode, list[ProviderName]] = {
    SearchMode.RECOVERY: [
        ProviderName.CACHE,
        ProviderName.SEARXNG,
        ProviderName.BRAVE,
        ProviderName.SERPER,
        ProviderName.TAVILY,
        ProviderName.EXA,
    ],
    SearchMode.DISCOVERY: [
        ProviderName.CACHE,
        ProviderName.SEARXNG,
        ProviderName.BRAVE,
        ProviderName.EXA,
        ProviderName.TAVILY,
        ProviderName.SERPER,
    ],
    SearchMode.GROUNDING: [
        ProviderName.CACHE,
        ProviderName.BRAVE,
        ProviderName.SERPER,
        ProviderName.SEARXNG,
    ],
    SearchMode.RESEARCH: [
        ProviderName.CACHE,
        ProviderName.TAVILY,
        ProviderName.EXA,
        ProviderName.BRAVE,
        ProviderName.SERPER,
    ],
}


def get_provider_order(mode: SearchMode) -> list[ProviderName]:
    """Return ordered provider list for a given search mode."""
    return ROUTING_POLICIES.get(mode, ROUTING_POLICIES[SearchMode.DISCOVERY])


def resolve_routing(
    mode: SearchMode,
    override_providers: list[ProviderName] | None,
) -> list[ProviderName]:
    """Resolve the final provider routing order.

    If caller overrides providers, use that list directly.
    Otherwise use the mode-based policy.
    """
    if override_providers:
        return list(override_providers)
    return get_provider_order(mode)
