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
    cache_policy_version: str
    extraction_plan_version: str
    quality_policy_version: str
    completeness_policy_version: str
    outcome_policy_version: str
    privacy_scope: str
    partial_allowed: bool

    @classmethod
    def from_accepted(cls, accepted):
        from argus.extraction.outcomes import AcceptedExtractionOutcome

        if not isinstance(accepted, AcceptedExtractionOutcome):
            raise TypeError("cache identity requires an accepted extraction")
        auth_refs = {
            provenance.authentication_scope_ref
            for provenance in (
                [
                    accepted.artifact.provenance
                    if accepted.artifact is not None
                    else None
                ]
                + [
                    step.provenance
                    for step in accepted.steps
                    if step.provenance is not None
                ]
            )
            if provenance is not None
            and provenance.authentication_scope_ref is not None
        }
        if len(auth_refs) > 1:
            raise ValueError("accepted extraction has inconsistent authentication scope")
        authentication_scope = next(iter(auth_refs), None)
        authentication_scope_fingerprint = (
            "anonymous"
            if authentication_scope is None
            else "sha256:"
            + hashlib.sha256(authentication_scope.encode("utf-8")).hexdigest()
        )
        return cls(
            normalized_url=accepted.normalized_url_identity
            or "sha256:"
            + hashlib.sha256(
                accepted.plan.normalized_url.encode("utf-8")
            ).hexdigest(),
            mode=accepted.plan.mode,
            access_scope=accepted.plan.access_scope,
            authentication_scope_fingerprint=authentication_scope_fingerprint,
            cache_policy_version=accepted.plan.cache_policy_ref,
            extraction_plan_version=accepted.plan.extraction_plan_version,
            quality_policy_version=accepted.plan.quality_policy_version,
            completeness_policy_version=(
                accepted.plan.completeness_policy_version
            ),
            outcome_policy_version=accepted.extraction_outcome_policy_version,
            privacy_scope=accepted.plan.privacy_scope,
            partial_allowed=accepted.plan.partial_allowed,
        )

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            {
                "normalized_url": self.normalized_url,
                "mode": self.mode,
                "access_scope": self.access_scope,
                "authentication_scope_fingerprint": (
                    self.authentication_scope_fingerprint
                ),
                "cache_policy_version": self.cache_policy_version,
                "extraction_plan_version": self.extraction_plan_version,
                "quality_policy_version": self.quality_policy_version,
                "completeness_policy_version": self.completeness_policy_version,
                "outcome_policy_version": self.outcome_policy_version,
                "privacy_scope": self.privacy_scope,
                "partial_allowed": self.partial_allowed,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


class ExtractionCache:
    def __init__(self, ttl_hours: int = 168, *, acceptance_repository=None):
        self._store: dict[str, tuple[object, float]] = {}
        self._ttl = ttl_hours * 3600
        self._acceptance_repository = acceptance_repository

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
    ) -> Optional[object]:
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
        content: object,
    ) -> None:
        if isinstance(url, ExtractionCacheIdentity):
            from argus.contracts import CanonicalOutcome
            from argus.extraction.outcomes import (
                AcceptedExtractionOutcome,
                ArtifactDisposition,
                ExtractionAcceptanceReceipt,
            )

            if (
                not isinstance(content, AcceptedExtractionOutcome)
                or not isinstance(
                    content.acceptance_receipt,
                    ExtractionAcceptanceReceipt,
                )
                or content.artifact is None
                or content.outcome
                not in {CanonicalOutcome.SUCCESS, CanonicalOutcome.DEGRADED}
                or content.artifact_disposition
                not in {
                    ArtifactDisposition.USABLE,
                    ArtifactDisposition.PARTIAL,
                }
                or (
                    content.artifact_disposition
                    is ArtifactDisposition.PARTIAL
                    and not url.partial_allowed
                )
                or ExtractionCacheIdentity.from_accepted(content) != url
            ):
                return
            loader = getattr(
                self._acceptance_repository,
                "load_extraction_outcome_by_receipt",
                None,
            )
            if not callable(loader):
                return
            durable = loader(content.acceptance_receipt.receipt_ref)
            if durable != content:
                return
            self._store[self._key(url)] = (content, time.time())
            return
        if not isinstance(content, ExtractedContent):
            return
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
