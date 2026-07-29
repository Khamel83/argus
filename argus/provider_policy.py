"""Canonical provider tier policy shared by execution and diagnostics."""

from __future__ import annotations

from argus.models import ProviderName


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
