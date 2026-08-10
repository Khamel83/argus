"""
In-memory content extraction cache.

Keys by normalized URL. TTL configurable via config (default 168h = 7 days).
Mirrors the SearchCache pattern from argus/broker/cache.py.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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
    cache_max_age_seconds: int = 604_800
    profile: str = "autonomous"
    effective_max_provider_tier: int = 3
    provider_restrictions: tuple[str, ...] = ()
    eligible_extractors: tuple[str, ...] = ()
    freshness_window_seconds: int = 604_800
    original_evidence_ref: str | None = None

    @classmethod
    def from_plan(
        cls,
        plan,
        *,
        outcome_policy_version: str,
        normalized_url_identity: str,
    ):
        return cls(
            normalized_url=normalized_url_identity,
            mode=plan.mode,
            access_scope=plan.access_scope,
            authentication_scope_fingerprint=(
                plan.authentication_scope.fingerprint
            ),
            cache_policy_version=plan.cache_policy_ref,
            extraction_plan_version=plan.extraction_plan_version,
            quality_policy_version=plan.quality_policy_version,
            completeness_policy_version=plan.completeness_policy_version,
            outcome_policy_version=outcome_policy_version,
            privacy_scope=plan.privacy_scope,
            partial_allowed=plan.partial_allowed,
            cache_max_age_seconds=plan.cache_max_age_seconds,
            profile=getattr(plan, "profile", "autonomous"),
            effective_max_provider_tier=getattr(
                plan, "effective_max_provider_tier", 3
            ),
            provider_restrictions=tuple(
                getattr(plan, "provider_restrictions", ())
            ),
            eligible_extractors=tuple(getattr(plan, "eligible_extractors", ())),
            freshness_window_seconds=getattr(
                plan, "freshness_window_seconds", plan.cache_max_age_seconds
            ),
            original_evidence_ref=getattr(plan, "original_evidence_ref", None),
        )

    @classmethod
    def from_accepted(cls, accepted):
        from argus.extraction.outcomes import AcceptedExtractionOutcome

        if not isinstance(accepted, AcceptedExtractionOutcome):
            raise TypeError("cache identity requires an accepted extraction")
        return cls.from_plan(
            accepted.plan,
            normalized_url_identity=accepted.normalized_url_identity
            or "sha256:"
            + hashlib.sha256(
                accepted.plan.normalized_url.encode("utf-8")
            ).hexdigest(),
            outcome_policy_version=accepted.extraction_outcome_policy_version,
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
                "cache_max_age_seconds": self.cache_max_age_seconds,
                "profile": self.profile,
                "effective_max_provider_tier": self.effective_max_provider_tier,
                "provider_restrictions": list(self.provider_restrictions),
                "eligible_extractors": list(self.eligible_extractors),
                "freshness_window_seconds": self.freshness_window_seconds,
                "original_evidence_ref": self.original_evidence_ref,
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
        *,
        acceptance_repository=None,
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
                acceptance_repository or self._acceptance_repository,
                "load_extraction_outcome_by_receipt",
                None,
            )
            if not callable(loader):
                return
            durable = loader(content.acceptance_receipt.receipt_ref)
            if durable != content:
                return
            # The local TTL guards process-memory retention; accepted-cache
            # eligibility itself derives age from the receipt timestamp in
            # ``decide`` so test/restart clocks cannot rewrite provenance.
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

    def decide(
        self,
        identity: ExtractionCacheIdentity,
        *,
        acceptance_repository=None,
        now: datetime | None = None,
    ):
        """Return a receipt-bound accepted cache decision for ``identity``.

        Exact policy identities are preferred.  A free profile may reuse a
        paid-origin artifact only when the immutable public/access policy is
        compatible, no provider restriction excludes its origin extractor,
        the origin is durably accepted, and both freshness windows hold.
        """
        from argus.extraction.outcomes import (
            AcceptedExtractionOutcome,
            CacheDecision,
            CacheOriginEvidence,
            CacheOutcome,
        )

        if not isinstance(identity, ExtractionCacheIdentity):
            raise TypeError("accepted cache lookup requires an identity")
        repository = acceptance_repository or self._acceptance_repository
        observed = now or datetime.now(timezone.utc)
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("accepted cache clock must be timezone-aware")
        observed = observed.astimezone(timezone.utc)

        candidates = []
        for content, stored_at in tuple(self._store.values()):
            if not isinstance(content, AcceptedExtractionOutcome):
                continue
            origin_identity = ExtractionCacheIdentity.from_accepted(content)
            if not _same_public_cache_context(identity, origin_identity):
                continue
            accepted_at = _parse_aware_timestamp(
                content.acceptance_receipt.accepted_at
            )
            if accepted_at is None:
                continue
            age = int((observed - accepted_at).total_seconds())
            if age < 0:
                continue
            candidates.append((content, origin_identity, accepted_at, age, stored_at))
        if not candidates:
            return CacheDecision(
                outcome=CacheOutcome.MISS,
                reason="no_cache_entry",
            )

        # Newest accepted origin wins deterministically; insertion time is only
        # a tie-breaker for same-second test fixtures.
        candidates.sort(key=lambda item: (item[2], item[4]), reverse=True)
        ineligible = None
        durable_unavailable = False
        for content, origin_identity, accepted_at, age, _stored_at in candidates:
            eligible, reason = _accepted_cache_eligibility(
                identity,
                origin_identity,
                content,
                age,
            )
            if not eligible:
                # Preserve the durable origin even when policy rejects reuse.
                # A fresh extraction may follow, but the accepted projection
                # still needs cache age/origin/reason diagnostics.
                if ineligible is None:
                    loader = getattr(
                        repository,
                        "load_extraction_outcome_by_receipt",
                        None,
                    )
                    if not callable(loader):
                        durable_unavailable = True
                    else:
                        durable = loader(content.acceptance_receipt.receipt_ref)
                        if durable == content:
                            ineligible = (
                                content,
                                age,
                                CacheOriginEvidence.from_accepted(
                                    content,
                                    acceptance_repository=repository,
                                ),
                            )
                        else:
                            durable_unavailable = True
                continue
            if not callable(
                getattr(repository, "load_extraction_outcome_by_receipt", None)
            ):
                return CacheDecision(
                    outcome=CacheOutcome.MISS,
                    reason="durable_origin_unavailable",
                )
            durable = repository.load_extraction_outcome_by_receipt(
                content.acceptance_receipt.receipt_ref
            )
            if durable != content:
                durable_unavailable = True
                continue
            origin_evidence = CacheOriginEvidence.from_accepted(
                content,
                acceptance_repository=repository,
            )
            return CacheDecision(
                outcome=CacheOutcome.HIT_ELIGIBLE,
                origin_run_ref=content.extraction_run_id,
                age_seconds=age,
                origin_evidence=origin_evidence,
                current_identity=identity,
                reason="eligible",
            )

        if ineligible is not None:
            content, age, origin_evidence = ineligible
            return CacheDecision(
                outcome=CacheOutcome.HIT_INELIGIBLE,
                origin_run_ref=content.extraction_run_id,
                age_seconds=age,
                origin_evidence=origin_evidence,
                current_identity=identity,
                reason="policy_ineligible",
            )
        if durable_unavailable:
            return CacheDecision(
                outcome=CacheOutcome.MISS,
                reason="durable_origin_unavailable",
            )
        return CacheDecision(
            outcome=CacheOutcome.HIT_INELIGIBLE,
            current_identity=identity,
            reason="policy_ineligible",
        )

    def clear(self) -> None:
        self._store.clear()

    def size(self) -> int:
        return len(self._store)


def _parse_aware_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _same_public_cache_context(
    current: ExtractionCacheIdentity,
    origin: ExtractionCacheIdentity,
) -> bool:
    return (
        current.normalized_url == origin.normalized_url
        and current.mode == origin.mode
        and current.access_scope == origin.access_scope
        and current.authentication_scope_fingerprint
        == origin.authentication_scope_fingerprint
        and current.cache_policy_version == origin.cache_policy_version
        and current.extraction_plan_version == origin.extraction_plan_version
        and current.quality_policy_version == origin.quality_policy_version
        and current.completeness_policy_version
        == origin.completeness_policy_version
        and current.outcome_policy_version == origin.outcome_policy_version
        and current.privacy_scope == origin.privacy_scope
        and current.partial_allowed == origin.partial_allowed
        and current.original_evidence_ref == origin.original_evidence_ref
    )


def _accepted_cache_eligibility(
    current: ExtractionCacheIdentity,
    origin: ExtractionCacheIdentity,
    content,
    age_seconds: int,
) -> tuple[bool, str]:
    from argus.extraction.outcomes import (
        ArtifactDisposition,
        ExtractorExecutionDecision,
    )

    if age_seconds > current.freshness_window_seconds:
        return False, "freshness_window"
    if age_seconds > current.cache_max_age_seconds:
        return False, "current_cache_stale"
    if age_seconds > origin.cache_max_age_seconds:
        return False, "origin_stale"
    if content.outcome.value not in {"success", "degraded"} or content.artifact_disposition not in {
        ArtifactDisposition.USABLE,
        ArtifactDisposition.PARTIAL,
    }:
        return False, "origin_not_usable"
    if (
        content.artifact_disposition is ArtifactDisposition.PARTIAL
        and not current.partial_allowed
    ):
        return False, "partial_not_allowed"
    if current.provider_restrictions:
        selected = content.selected_extractor
        if selected not in current.provider_restrictions:
            return False, "provider_restricted"
    if current.eligible_extractors:
        selected = content.selected_extractor
        if selected not in current.eligible_extractors:
            return False, "extractor_ineligible"
    selected = content.selected_extractor
    if (
        origin.provider_restrictions
        and selected not in origin.provider_restrictions
    ):
        return False, "origin_provider_restricted"
    if origin.eligible_extractors and selected not in origin.eligible_extractors:
        return False, "origin_extractor_ineligible"
    exact = current == origin
    free_cross_boundary = (
        current.profile == "free"
        and current.effective_max_provider_tier == 0
        and origin.profile != "free"
    )
    if not exact and not free_cross_boundary:
        return False, "policy_boundary"
    invoked = tuple(
        step
        for step in content.steps
        if step.decision is ExtractorExecutionDecision.INVOKED
    )
    if not invoked or any(step.spend is None for step in invoked):
        return False, "origin_spend_unavailable"
    return True, "eligible"
