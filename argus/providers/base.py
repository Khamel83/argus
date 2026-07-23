"""
Provider adapter contract.

All provider adapters must implement this interface.
Provider-specific response shapes must never leak outside adapters.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import List
from argus.models import (
    ProviderName,
    ProviderStatus,
    ProviderTrace,
    SearchResult,
    SearchQuery,
)


class ProbeCapability(str, Enum):
    ASYNC_NATIVE = "async_native"
    BLOCKING_UNSUPPORTED = "blocking_unsupported"


class BaseProvider(ABC):
    """Abstract base for all search provider adapters."""

    probe_capability = ProbeCapability.ASYNC_NATIVE

    @property
    @abstractmethod
    def name(self) -> ProviderName:
        """Unique provider identifier."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is configured and ready to use."""
        ...

    @abstractmethod
    def status(self) -> ProviderStatus:
        """Return current operational status."""
        ...

    @abstractmethod
    async def search(
        self, query: SearchQuery
    ) -> tuple[List[SearchResult], ProviderTrace]:
        """Execute a search and return normalized results with trace metadata."""
        ...
