"""
In-memory content extraction cache.

Keys by normalized URL. TTL configurable via config (default 168h = 7 days).
Mirrors the SearchCache pattern from argus/broker/cache.py.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Optional

from argus.extraction.models import ExtractedContent


@dataclass(frozen=True, slots=True)
class ExtractionCacheIdentity:
    """Policy-complete cache identity; URL alone is never enough at the new seam."""

    normalized_url: str
    mode: str
    access_scope: str
    authentication_scope_fingerprint: str
    extraction_plan_version: str
    quality_policy_version: str
    completeness_policy_version: str
    partial_allowed: bool

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            {
                "normalized_url": self.normalized_url.strip().rstrip("/"),
                "mode": self.mode,
                "access_scope": self.access_scope,
                "authentication_scope_fingerprint": (
                    self.authentication_scope_fingerprint
                ),
                "extraction_plan_version": self.extraction_plan_version,
                "quality_policy_version": self.quality_policy_version,
                "completeness_policy_version": self.completeness_policy_version,
                "partial_allowed": self.partial_allowed,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


class ExtractionCache:
    def __init__(self, ttl_hours: int = 168):
        self._store: dict[str, tuple[ExtractedContent, float]] = {}
        self._ttl = ttl_hours * 3600

    @staticmethod
    def _key(url: str | ExtractionCacheIdentity) -> str:
        if isinstance(url, ExtractionCacheIdentity):
            return hashlib.sha256(url.canonical_bytes()).hexdigest()
        normalized = url.strip().rstrip("/")
        if not normalized.startswith("http"):
            normalized = "https://" + normalized
        return hashlib.sha256(normalized.encode()).hexdigest()

    def get(
        self,
        url: str | ExtractionCacheIdentity,
    ) -> Optional[ExtractedContent]:
        key = self._key(url)
        if key in self._store:
            content, ts = self._store[key]
            if time.time() - ts < self._ttl:
                return content
            del self._store[key]
        return None

    def put(
        self,
        url: str | ExtractionCacheIdentity,
        content: ExtractedContent,
    ) -> None:
        if content.error or not content.text or content.quality_passed is not True:
            return
        disposition = getattr(content, "artifact_disposition", None)
        if disposition is not None:
            from argus.extraction.outcomes import ArtifactDisposition

            if disposition not in {
                ArtifactDisposition.USABLE,
                ArtifactDisposition.PARTIAL,
            }:
                return
            if (
                disposition is ArtifactDisposition.PARTIAL
                and (
                    not isinstance(url, ExtractionCacheIdentity)
                    or not url.partial_allowed
                )
            ):
                return
        elif (
            isinstance(url, ExtractionCacheIdentity)
            and content.completeness_result is not None
            and not content.completeness_result.is_complete
            and not url.partial_allowed
        ):
            return
        key = self._key(url)
        self._store[key] = (content, time.time())

    def clear(self) -> None:
        self._store.clear()

    def size(self) -> int:
        return len(self._store)
