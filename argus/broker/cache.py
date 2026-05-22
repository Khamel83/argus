"""
In-memory query cache.

Keys by normalized query + mode. TTL configurable via config.
"""

import hashlib
import time
from typing import Optional

from argus.models import SearchMode, SearchResponse


class SearchCache:
    def __init__(self, ttl_hours: int = 168):
        self._store: dict[str, tuple[SearchResponse, float]] = {}
        self._ttl = ttl_hours * 3600

    def _key(self, query: str, mode: SearchMode, include_attribution: bool = False) -> str:
        normalized = query.strip().lower()
        raw = f"{normalized}:{mode.value}:attribution={include_attribution}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(
        self,
        query: str,
        mode: SearchMode,
        include_attribution: bool = False,
    ) -> Optional[SearchResponse]:
        key = self._key(query, mode, include_attribution=include_attribution)
        if key in self._store:
            response, ts = self._store[key]
            if time.time() - ts < self._ttl:
                return response
            del self._store[key]
        return None

    def put(
        self,
        query: str,
        mode: SearchMode,
        response: SearchResponse,
        include_attribution: bool = False,
    ) -> None:
        key = self._key(query, mode, include_attribution=include_attribution)
        self._store[key] = (response, time.time())

    def clear(self) -> None:
        self._store.clear()

    def size(self) -> int:
        return len(self._store)
