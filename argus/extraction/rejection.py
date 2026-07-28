"""Stable, privacy-safe extraction rejection classification."""

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Iterable

from argus.extraction.models import ExtractedContent, ExtractionAttempt


class RejectionCode(str, Enum):
    """Caller-facing reasons an extraction was not accepted as usable."""

    QUALITY_GATE_FAILED = "quality_gate_failed"
    INCOMPLETE_CONTENT = "incomplete_content"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    PARSE_ERROR = "parse_error"
    UNSUPPORTED_SOURCE = "unsupported_source"
    RATE_LIMITED = "rate_limited"
    EMPTY_RESULT = "empty_result"


class RejectionAction(str, Enum):
    """Bounded caller guidance that does not require exposing raw failures."""

    RETRY_LATER = "retry_later"
    TERMINAL = "terminal"
    FALLBACK_PROVIDER = "fallback_provider"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True)
class ExtractionRejection:
    """A bounded rejection record safe to return and persist."""

    code: RejectionCode
    provider: str | None
    quality_passed: bool | None
    is_complete: bool | None
    recommended_action: RejectionAction
    attempt_count: int
    last_status: str | None
    total_latency_ms: int

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def rejection_ref(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )
        return "rejection:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


_TIMEOUT_MARKERS = ("timeout", "timed out")
_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "too many requests", "http 429")
_UNSUPPORTED_MARKERS = (
    "unsupported",
    "invalid video url",
    "invalid source",
    "ssrf_blocked",
)
_UNAVAILABLE_MARKERS = (
    "unavailable",
    "not configured",
    "no api key",
    "binary not found",
    "browser unavailable",
    "connection refused",
    "connecterror",
)
_PARSE_MARKERS = ("parse", "decode", "malformed", "no content extracted")
_EMPTY_MARKERS = (
    "empty",
    "no_content",
    "no content",
    "no result",
    "no_result",
    "too short",
)
_SAFE_LABEL = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _safe_label(value: str | None) -> str | None:
    if value and _SAFE_LABEL.fullmatch(value):
        return value
    return None


def _matching_attempt(
    attempts: Iterable[ExtractionAttempt],
    markers: tuple[str, ...],
) -> ExtractionAttempt | None:
    for attempt in attempts:
        summary = (attempt.failure_summary or "").lower()
        if any(marker in summary for marker in markers):
            return attempt
    return None


def _rejection(
    result: ExtractedContent,
    *,
    code: RejectionCode,
    action: RejectionAction,
    attempt: ExtractionAttempt | None = None,
) -> ExtractionRejection:
    attempts = list(result.attempts)
    selected = result.extractor.value if result.extractor else None
    selected_attempt = next(
        (
            item
            for item in reversed(attempts)
            if selected is not None and item.extractor == selected
        ),
        None,
    )
    relevant_attempt = attempt or selected_attempt
    if relevant_attempt is None and selected is None and attempts:
        relevant_attempt = attempts[-1]
    provider = relevant_attempt.extractor if relevant_attempt is not None else selected
    last_status = relevant_attempt.status if relevant_attempt is not None else None
    completeness = result.completeness_result
    quality_passed = bool(result.quality_passed) if result.text else None
    return ExtractionRejection(
        code=code,
        provider=_safe_label(provider),
        quality_passed=quality_passed,
        is_complete=completeness.is_complete if completeness else None,
        recommended_action=action,
        attempt_count=len(attempts),
        last_status=_safe_label(last_status),
        total_latency_ms=min(
            sum(max(0, int(item.latency_ms)) for item in attempts),
            2_147_483_647,
        ),
    )


def classify_extraction_rejection(
    result: ExtractedContent,
) -> ExtractionRejection | None:
    """Classify an unusable result without copying URL, content, or raw errors."""
    if result.text and not result.quality_passed:
        quality_attempt = next(
            (
                attempt
                for attempt in reversed(result.attempts)
                if attempt.status == "quality_failed"
            ),
            None,
        )
        return _rejection(
            result,
            code=RejectionCode.QUALITY_GATE_FAILED,
            action=RejectionAction.TERMINAL,
            attempt=quality_attempt,
        )

    completeness = result.completeness_result
    if completeness is not None and not completeness.is_complete:
        return _rejection(
            result,
            code=RejectionCode.INCOMPLETE_CONTENT,
            action=RejectionAction.RETRY_LATER,
        )

    if not result.error:
        if result.text:
            return None
        return _rejection(
            result,
            code=RejectionCode.EMPTY_RESULT,
            action=RejectionAction.RETRY_LATER,
        )

    classifications = (
        (RejectionCode.TIMEOUT, RejectionAction.RETRY_LATER, _TIMEOUT_MARKERS),
        (
            RejectionCode.RATE_LIMITED,
            RejectionAction.RETRY_LATER,
            _RATE_LIMIT_MARKERS,
        ),
        (
            RejectionCode.UNSUPPORTED_SOURCE,
            RejectionAction.TERMINAL,
            _UNSUPPORTED_MARKERS,
        ),
        (
            RejectionCode.PROVIDER_UNAVAILABLE,
            RejectionAction.RETRY_LATER,
            _UNAVAILABLE_MARKERS,
        ),
        (
            RejectionCode.PARSE_ERROR,
            RejectionAction.FALLBACK_PROVIDER,
            _PARSE_MARKERS,
        ),
        (
            RejectionCode.EMPTY_RESULT,
            RejectionAction.FALLBACK_PROVIDER,
            _EMPTY_MARKERS,
        ),
    )
    attempts = list(result.attempts)
    for code, action, markers in classifications:
        attempt = _matching_attempt(attempts, markers)
        if attempt is not None:
            return _rejection(result, code=code, action=action, attempt=attempt)
        error = result.error.lower()
        if any(marker in error for marker in markers):
            return _rejection(result, code=code, action=action)

    return _rejection(
        result,
        code=RejectionCode.PROVIDER_UNAVAILABLE,
        action=RejectionAction.RETRY_LATER,
    )


def classify_typed_extraction_rejection(facts) -> ExtractionRejection:
    """Issue #57 mapper for the normalized S3 fact seam."""
    from argus.extraction.outcomes import (
        AttemptOutcome,
        ExtractionContractRejected,
        RejectionSourceKind,
        RejectionFacts,
    )

    if not isinstance(facts, RejectionFacts):
        raise ExtractionContractRejected()
    if facts.source_kind is RejectionSourceKind.ARTIFACT_QUALITY:
        code = RejectionCode.QUALITY_GATE_FAILED
    elif facts.source_kind is RejectionSourceKind.ARTIFACT_INCOMPLETE:
        code = RejectionCode.INCOMPLETE_CONTENT
    elif facts.source_kind is RejectionSourceKind.OPERATION_DEADLINE:
        code = RejectionCode.TIMEOUT
    elif facts.source_kind is RejectionSourceKind.PREFLIGHT:
        code = (
            RejectionCode.UNSUPPORTED_SOURCE
            if facts.terminal_outcome.value == "policy_rejected"
            else RejectionCode.PROVIDER_UNAVAILABLE
        )
    else:
        attempt_codes = {
            AttemptOutcome.ADAPTER_REQUEST_REJECTED: RejectionCode.PARSE_ERROR,
            AttemptOutcome.PROVIDER_AUTHENTICATION_REJECTED: (
                RejectionCode.PROVIDER_UNAVAILABLE
            ),
            AttemptOutcome.PROVIDER_POLICY_REJECTED: (
                RejectionCode.UNSUPPORTED_SOURCE
            ),
            AttemptOutcome.EMPTY: RejectionCode.EMPTY_RESULT,
            AttemptOutcome.RATE_LIMITED: RejectionCode.RATE_LIMITED,
            AttemptOutcome.BALANCE_EXHAUSTED: RejectionCode.PROVIDER_UNAVAILABLE,
            AttemptOutcome.TIMEOUT: RejectionCode.TIMEOUT,
            AttemptOutcome.PROVIDER_UNAVAILABLE: (
                RejectionCode.PROVIDER_UNAVAILABLE
            ),
            AttemptOutcome.PARSE_ERROR: RejectionCode.PARSE_ERROR,
            AttemptOutcome.UNKNOWN_FAILURE: RejectionCode.PROVIDER_UNAVAILABLE,
            AttemptOutcome.CONTENT: RejectionCode.PROVIDER_UNAVAILABLE,
        }
        codes = {attempt_codes[outcome] for outcome in facts.attempt_outcomes}
        code = (
            codes.pop()
            if len(codes) == 1
            else RejectionCode.PROVIDER_UNAVAILABLE
        )
    if facts.autonomous and code in {
        RejectionCode.QUALITY_GATE_FAILED,
        RejectionCode.UNSUPPORTED_SOURCE,
        RejectionCode.PROVIDER_UNAVAILABLE,
    }:
        action = RejectionAction.TERMINAL
    elif (
        facts.eligible_fallback_remains
        and code
        in {
            RejectionCode.PARSE_ERROR,
            RejectionCode.EMPTY_RESULT,
            RejectionCode.PROVIDER_UNAVAILABLE,
        }
    ):
        action = RejectionAction.FALLBACK_PROVIDER
    elif code in {
        RejectionCode.TIMEOUT,
        RejectionCode.RATE_LIMITED,
        RejectionCode.INCOMPLETE_CONTENT,
        RejectionCode.EMPTY_RESULT,
    }:
        action = RejectionAction.RETRY_LATER
    else:
        action = RejectionAction.TERMINAL
    return ExtractionRejection(
        code=code,
        provider=facts.provider,
        quality_passed=facts.quality_passed,
        is_complete=facts.is_complete,
        recommended_action=action,
        attempt_count=facts.attempt_count,
        last_status=facts.last_status,
        total_latency_ms=facts.total_latency_ms,
    )


def validate_typed_extraction_rejection(facts, rejection) -> None:
    """Validate one mapper result against its complete typed source facts."""
    from argus.extraction.outcomes import (
        ExtractionContractRejected,
        RejectionFacts,
    )

    if not isinstance(facts, RejectionFacts) or not isinstance(
        rejection,
        ExtractionRejection,
    ):
        raise ExtractionContractRejected()
    expected = classify_typed_extraction_rejection(facts)
    if rejection != expected:
        raise ExtractionContractRejected()
