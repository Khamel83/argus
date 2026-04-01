"""Generic in-memory TTL cache.

Stores arbitrary values keyed by a caller-supplied key function.
Expired entries are evicted on access (lazy) — no background threads.
"""

import hashlib
import time
from typing import Callable, Optional, TypeVar

V = TypeVar("V")


class TTLCache:
    def __init__(
        self,
        *,
        ttl_seconds: float,
        key_fn: Callable[..., str],
        skip_fn: Callable[[V], bool] | None = None,
    ):
        self._store: dict[str, tuple[V, float]] = {}
        self._ttl = ttl_seconds
        self._key_fn = key_fn
        self._skip_fn = skip_fn

    def get(self, *key_args) -> Optional[V]:
        key = self._key_fn(*key_args)
        if key in self._store:
            value, ts = self._store[key]
            if time.time() - ts < self._ttl:
                return value
            del self._store[key]
        return None

    def put(self, *key_args, value: V) -> None:
        if self._skip_fn and self._skip_fn(value):
            return
        key = self._key_fn(*key_args)
        self._store[key] = (value, time.time())

    def clear(self) -> None:
        self._store.clear()

    def size(self) -> int:
        return len(self._store)


# --- Key helpers ---

def hash_key(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode()).hexdigest()


def search_cache_key(query: str, mode) -> str:
    normalized = query.strip().lower()
    return hash_key(normalized, mode.value)


def extraction_cache_key(url: str) -> str:
    normalized = url.strip().rstrip("/")
    if not normalized.startswith("http"):
        normalized = "https://" + normalized
    return hash_key(normalized)
