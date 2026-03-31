"""
Provider adapter contract.

All provider adapters must implement this interface.
Provider-specific response shapes must never leak outside adapters.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from argus.models import ProviderName, ProviderStatus, ProviderTrace, SearchResult, SearchQuery


class BaseProvider(ABC):
    """Abstract base for all search provider adapters."""

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
    async def search(self, query: SearchQuery) -> tuple[List[SearchResult], ProviderTrace]:
        """Execute a search and return normalized results with trace metadata."""
        ...
