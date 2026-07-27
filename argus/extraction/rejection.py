"""Stable, privacy-safe extraction rejection classification."""

from dataclasses import asdict, dataclass
from enum import Enum
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
