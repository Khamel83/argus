"""
You.com provider stub.

Not yet implemented. Returns empty results.
"""

import time
from typing import List, Tuple

from argus.config import ProviderConfig
from argus.models import (
    ProviderName,
    ProviderStatus,
    ProviderTrace,
    SearchResult,
    SearchQuery,
)
from argus.providers.base import BaseProvider


class YouProvider(BaseProvider):
    """Stub — not yet implemented."""

    def __init__(self, config: ProviderConfig):
        self._config = config

    @property
    def name(self) -> ProviderName:
        return ProviderName.YOU

    def is_available(self) -> bool:
        return False

    def status(self) -> ProviderStatus:
        return ProviderStatus.DISABLED_BY_CONFIG

    async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]:
        return [], ProviderTrace(
            provider=self.name,
            status="skipped",
            error="You.com provider not implemented",
        )
