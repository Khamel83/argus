"""Canonical provider adapter identities used by fixture attestations."""

from __future__ import annotations

import importlib

from argus.models import ProviderName


CANONICAL_ADAPTERS = {
    ProviderName.BRAVE: ("argus.providers.brave", "BraveProvider"),
    ProviderName.DUCKDUCKGO: (
        "argus.providers.duckduckgo", "DuckDuckGoProvider"
    ),
    ProviderName.EXA: ("argus.providers.exa", "ExaProvider"),
    ProviderName.GITHUB: ("argus.providers.github", "GitHubProvider"),
    ProviderName.LINKUP: ("argus.providers.linkup", "LinkupProvider"),
    ProviderName.PARALLEL: ("argus.providers.parallel", "ParallelProvider"),
    ProviderName.SEARCHAPI: ("argus.providers.searchapi", "SearchApiProvider"),
    ProviderName.SEARXNG: ("argus.providers.searxng", "SearXNGProvider"),
    ProviderName.SERPER: ("argus.providers.serper", "SerperProvider"),
    ProviderName.TAVILY: ("argus.providers.tavily", "TavilyProvider"),
    ProviderName.VALYU: ("argus.providers.valyu", "ValyuProvider"),
    ProviderName.WOLFRAM: ("argus.providers.wolfram", "WolframProvider"),
    ProviderName.YAHOO: ("argus.providers.yahoo", "YahooProvider"),
    ProviderName.YOU: ("argus.providers.you", "YouProvider"),
}


def canonical_adapter(provider: ProviderName):
    module_name, class_name = CANONICAL_ADAPTERS[provider]
    module = importlib.import_module(module_name)
    return module_name, class_name, module, getattr(module, class_name)
