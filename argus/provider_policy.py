"""Canonical provider tier policy shared by execution and diagnostics."""

from __future__ import annotations

from argus.models import ProviderName, is_adapter_provider


PROVIDER_TIERS: dict[ProviderName, int] = {
    ProviderName.SEARXNG: 0,
    ProviderName.DUCKDUCKGO: 0,
    ProviderName.YAHOO: 0,
    ProviderName.GITHUB: 0,
    ProviderName.WOLFRAM: 0,
    ProviderName.ARCHIVE: 0,
    ProviderName.BRAVE: 1,
    ProviderName.TAVILY: 1,
    ProviderName.LINKUP: 1,
    ProviderName.EXA: 1,
    ProviderName.PARALLEL: 1,
    ProviderName.SERPER: 3,
    ProviderName.YOU: 3,
    ProviderName.SEARCHAPI: 3,
    ProviderName.VALYU: 3,
}

ONE_TIME_CREDIT_PROVIDERS = frozenset(
    provider for provider, tier in PROVIDER_TIERS.items() if tier == 3
)

# Registration metadata is deliberately separate from adapter availability.
# These values describe the contract that an operator must bind before a
# provider can be considered executable.  They never make a network call and
# they never imply that the provider is configured or healthy.
PROVIDER_BILLING_CLASSES: dict[ProviderName, str] = {
    provider: (
        "free" if tier == 0 else
        "monthly" if tier == 1 else
        "one_time"
    )
    for provider, tier in PROVIDER_TIERS.items()
    if is_adapter_provider(provider)
}

# ``search_and_extract`` identifies providers whose account also exposes a
# documented contents endpoint.  ``computed_answer`` is WolframAlpha's
# grounding-only answer API; every other adapter is search-only.
PROVIDER_EXTRACTION_CAPABILITIES: dict[ProviderName, str] = {
    provider: "search_and_extract"
    if provider in {ProviderName.VALYU, ProviderName.YOU}
    else "computed_answer"
    if provider is ProviderName.WOLFRAM
    else "search_only"
    for provider in PROVIDER_BILLING_CLASSES
}

SUPPORTED_BILLING_CLASSES = frozenset(PROVIDER_BILLING_CLASSES.values())
SUPPORTED_EXTRACTION_CAPABILITIES = frozenset(
    PROVIDER_EXTRACTION_CAPABILITIES.values()
)


def provider_billing_class(provider: ProviderName) -> str:
    """Return the static billing class for one catalog adapter."""
    return PROVIDER_BILLING_CLASSES[provider]


def provider_extraction_capability(provider: ProviderName) -> str:
    """Return the static extraction capability for one catalog adapter."""
    return PROVIDER_EXTRACTION_CAPABILITIES[provider]
