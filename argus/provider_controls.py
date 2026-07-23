"""Canonical environment prefixes for provider enablement controls."""

from argus.models import ProviderName


PROVIDER_ENV_PREFIXES = tuple(
    provider.value.upper()
    for provider in ProviderName
    if provider is not ProviderName.CACHE
)
EXTRACTION_PROVIDER_ENV_PREFIXES = ("JINA", "FIRECRAWL")
HERMETIC_PROVIDER_ENV_PREFIXES = (
    *PROVIDER_ENV_PREFIXES,
    *EXTRACTION_PROVIDER_ENV_PREFIXES,
)
