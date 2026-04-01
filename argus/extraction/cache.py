"""
In-memory content extraction cache.

Keys by normalized URL. TTL configurable via config (default 168h = 7 days).
Mirrors the SearchCache pattern from argus/broker/cache.py.
"""

import hashlib
import time
from typing import Optional

from argus.extraction.models import ExtractedContent


class ExtractionCache:
    def __init__(self, ttl_hours: int = 168):
        self._store: dict[str, tuple[ExtractedContent, float]] = {}
        self._ttl = ttl_hours * 3600

    @staticmethod
    def _key(url: str) -> str:
        normalized = url.strip().rstrip("/")
        if not normalized.startswith("http"):
            normalized = "https://" + normalized
        return hashlib.sha256(normalized.encode()).hexdigest()

    def get(self, url: str) -> Optional[ExtractedContent]:
        key = self._key(url)
        if key in self._store:
            content, ts = self._store[key]
            if time.time() - ts < self._ttl:
                return content
            del self._store[key]
        return None

    def put(self, url: str, content: ExtractedContent) -> None:
        if content.error:
            return
        key = self._key(url)
        self._store[key] = (content, time.time())

    def clear(self) -> None:
        self._store.clear()

    def size(self) -> int:
        return len(self._store)
