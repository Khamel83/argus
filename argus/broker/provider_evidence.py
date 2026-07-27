"""Bounded, private provider-response evidence.

Provider-native payloads terminate in this module.  Only the closed, bounded
projection below may cross into broker execution, logs, or persistence.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Awaitable, Mapping, Sequence, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from argus.models import ProviderName, ProviderTrace, SearchResult

MAX_URL = 8_192
MAX_TITLE = 1_000
MAX_SNIPPET = 2_000
MAX_HIGHLIGHTS = 3
MAX_HIGHLIGHT = 500
MAX_LABEL = 64
MAX_REFERENCE = 128
MAX_WARNING = 256
MAX_WARNINGS = 5
MAX_RAW_DATE = 128
MAX_REDIRECTS = 3
MAX_USAGE_COUNT = 1_000_000_000_000
MAX_COST_USD = 1_000_000_000
MAX_RATE_LIMIT_REMAINING = 1_000_000_000_000
MAX_RETRY_AFTER_SECONDS = 31_536_000
MAX_RATE_RESET_AHEAD_SECONDS = 366 * 24 * 60 * 60

_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|appid|authorization|cookie|credential|password|"
    r"secret|signature|signed|token|x-amz-|x-goog-)",
    re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"(?:authorization\s*:|bearer\s+|cookie\s*:|set-cookie\s*:|"
    r"(?:api[_-]?key|appid|credential|password|secret|signature|token)\s*[=:]|"
    r"/(?:Users|home|Volumes)/[^\s]+|[A-Z]:\\\\Users\\\\)",
    re.IGNORECASE,
)


class EvidenceKind(str, Enum):
    WEB_PAGE = "web_page"
    NEWS = "news"
    REPOSITORY = "repository"
    COMPUTED_ANSWER = "computed_answer"
    SOURCED_ANSWER = "sourced_answer"
    PAPER = "paper"
    PROPRIETARY = "proprietary"
    ARCHIVE = "archive"
    UNKNOWN = "unknown"


class SnippetKind(str, Enum):
    PROVIDER_DESCRIPTION = "provider_description"
    PROVIDER_SNIPPET = "provider_snippet"
    PROVIDER_HIGHLIGHT = "provider_highlight"
    PROVIDER_TEXT_EXCERPT = "provider_text_excerpt"
    EMPTY = "empty"


class PublicationPrecision(str, Enum):
    TIMESTAMP = "timestamp"
    DATE = "date"
    MONTH = "month"
    YEAR = "year"
    PROVIDER_AGE = "provider_age"
    UNKNOWN = "unknown"


class PublicationSource(str, Enum):
    PROVIDER_FIELD = "provider_field"
    PROVIDER_AGE = "provider_age"
    RESULT_TEXT = "result_text"
    NONE = "none"


class ContractConfidence(str, Enum):
    OFFICIAL_CONTRACT = "official_contract"
    OWNED_LIBRARY_CONTRACT = "owned_library_contract"
    FIXTURE_BACKED = "fixture_backed"
    UNVERIFIED = "unverified"


class NativeScoreSemantics(str, Enum):
    RELEVANCE = "relevance"
    PROVIDER_RANK_SCORE = "provider_rank_score"
    QUALITY = "quality"
    UNKNOWN = "unknown"


class QueryRelation(str, Enum):
    EXACT = "exact"
    PROVIDER_REWRITE = "provider_rewrite"
    UNKNOWN = "unknown"


class TranslationPrecision(str, Enum):
    EXACT = "exact"
    WIDENED = "widened"
    UNSUPPORTED = "unsupported"


class FilterStrength(str, Enum):
    STRICT_CONTRACT = "strict_contract"
    BEST_EFFORT = "best_effort"
    UNKNOWN = "unknown"


class FailureCategory(str, Enum):
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_REJECTED = "authentication_rejected"
    POLICY_REJECTED = "policy_rejected"
    RATE_LIMITED = "rate_limited"
    BALANCE_EXHAUSTED = "balance_exhausted"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PARSE_ERROR = "parse_error"
    EMPTY = "empty"


class EgressType(str, Enum):
    RESIDENTIAL = "residential"
    DATACENTER = "datacenter"
    LOCAL = "local"
    UNKNOWN = "unknown"


def _bounded_text(value: object, limit: int) -> tuple[str, bool]:
    if value is None:
        return "", False
    if not isinstance(value, str):
        return "", False
    text = value
    if _SECRET_TEXT.search(text):
        return "", False
    return text[:limit], len(text) > limit


def _bounded_label(value: object, limit: int = MAX_LABEL) -> str | None:
    if not isinstance(value, str) or not value or len(value) > limit:
        return None
    if (
        not value.isprintable()
        or _SECRET_TEXT.search(value)
        or _SECRET_KEY.search(value)
    ):
        return None
    return value


def _bounded_reference(value: object) -> str | None:
    return _bounded_label(value, MAX_REFERENCE)


def _finite_nonnegative(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) and normalized >= 0 else None


def _bounded_nonnegative(value: object, maximum: float) -> float | None:
    normalized = _finite_nonnegative(value)
    if normalized is None or normalized > maximum:
        return None
    return normalized


def _validated_rate_reset(
    value: object, *, observed_at: datetime
) -> datetime | None:
    if value is None:
        return None
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
    ):
        raise ValueError("rate reset and observation must be timezone-aware")
    reset = value.astimezone(timezone.utc)
    observed = observed_at.astimezone(timezone.utc)
    try:
        latest = observed + timedelta(seconds=MAX_RATE_RESET_AHEAD_SECONDS)
    except OverflowError as error:
        raise ValueError("rate reset reference is implausible") from error
    if reset < observed or reset > latest:
        raise ValueError("rate reset must not be backward or implausibly far")
    return reset


def _safe_url(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_URL:
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
        return None
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not _SECRET_KEY.search(key)
    ]
    netloc = parts.hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit(
        (parts.scheme, netloc, parts.path, urlencode(query, doseq=True), "")
    )


def query_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SnippetEvidence:
    primary_text: str
    kind: SnippetKind
    highlights: tuple[str, ...] = ()
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SnippetKind):
            raise TypeError("snippet kind must be closed")
        if not isinstance(self.highlights, tuple):
            raise TypeError("snippet highlights must be a tuple")
        primary, truncated = _bounded_text(self.primary_text, MAX_SNIPPET)
        raw_highlights = tuple(self.highlights)
        highlights: list[str] = []
        highlight_truncated = False
        for raw in raw_highlights[:MAX_HIGHLIGHTS]:
            text, was_truncated = _bounded_text(raw, MAX_HIGHLIGHT)
            if text:
                highlights.append(text)
            highlight_truncated |= was_truncated
        object.__setattr__(self, "primary_text", primary)
        object.__setattr__(self, "highlights", tuple(highlights))
        object.__setattr__(
            self,
            "truncated",
            self.truncated
            or truncated
            or highlight_truncated
            or len(raw_highlights) > MAX_HIGHLIGHTS,
        )


@dataclass(frozen=True, slots=True)
class PublicationEvidence:
    published_at_utc: datetime | None = None
    published_date: date | None = None
    precision: PublicationPrecision = PublicationPrecision.UNKNOWN
    source: PublicationSource = PublicationSource.NONE
    contract_confidence: ContractConfidence = ContractConfidence.UNVERIFIED
    raw_field_name: str | None = None
    semantic_contract_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.precision, PublicationPrecision):
            raise TypeError("publication precision must be closed")
        if not isinstance(self.source, PublicationSource):
            raise TypeError("publication source must be closed")
        if not isinstance(self.contract_confidence, ContractConfidence):
            raise TypeError("publication confidence must be closed")
        if self.published_at_utc is not None:
            if (
                not isinstance(self.published_at_utc, datetime)
                or self.published_at_utc.tzinfo is None
            ):
                raise ValueError("publication timestamp must be timezone-aware")
            object.__setattr__(
                self,
                "published_at_utc",
                self.published_at_utc.astimezone(timezone.utc),
            )
        if self.published_date is not None and not isinstance(
            self.published_date, date
        ):
            raise TypeError("publication date must be a date")
        if self.published_at_utc is not None and self.published_date is not None:
            raise ValueError("publication evidence must have one temporal value")
        object.__setattr__(self, "raw_field_name", _bounded_label(self.raw_field_name))
        object.__setattr__(
            self,
            "semantic_contract_ref",
            _bounded_reference(self.semantic_contract_ref),
        )

    @classmethod
    def from_raw(
        cls,
        value: object,
        *,
        raw_field_name: str,
        confidence: ContractConfidence,
        semantic_contract_ref: str | None = None,
    ) -> PublicationEvidence | None:
        if not isinstance(value, str) or not value or len(value) > MAX_RAW_DATE:
            return None
        field = _bounded_label(raw_field_name)
        if field is None:
            return None
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return cls(
                    published_date=date.fromisoformat(value),
                    precision=PublicationPrecision.DATE,
                    source=PublicationSource.PROVIDER_FIELD,
                    contract_confidence=confidence,
                    raw_field_name=field,
                    semantic_contract_ref=_bounded_reference(semantic_contract_ref),
                )
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            return cls(
                published_at_utc=parsed.astimezone(timezone.utc),
                precision=PublicationPrecision.TIMESTAMP,
                source=PublicationSource.PROVIDER_FIELD,
                contract_confidence=confidence,
                raw_field_name=field,
                semantic_contract_ref=_bounded_reference(semantic_contract_ref),
            )
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class NativeScoreEvidence:
    value: float
    semantics: NativeScoreSemantics
    scale_min: float | None = None
    scale_max: float | None = None
    contract_confidence: ContractConfidence = ContractConfidence.UNVERIFIED

    def __post_init__(self) -> None:
        value = _finite_nonnegative(self.value)
        if value is None:
            raise ValueError("native score must be finite and non-negative")
        if not isinstance(self.semantics, NativeScoreSemantics):
            raise TypeError("native score semantics must be closed")
        if not isinstance(self.contract_confidence, ContractConfidence):
            raise TypeError("native score confidence must be closed")
        for name in ("scale_min", "scale_max"):
            raw = getattr(self, name)
            if raw is not None and _finite_nonnegative(raw) is None:
                raise ValueError("native score scale must be finite")

    @classmethod
    def from_value(
        cls,
        value: object,
        *,
        semantics: NativeScoreSemantics,
        confidence: ContractConfidence = ContractConfidence.UNVERIFIED,
        scale_min: float | None = None,
        scale_max: float | None = None,
    ) -> NativeScoreEvidence | None:
        normalized = _finite_nonnegative(value)
        if normalized is None:
            return None
        return cls(
            normalized,
            semantics,
            _finite_nonnegative(scale_min),
            _finite_nonnegative(scale_max),
            confidence,
        )


@dataclass(frozen=True, slots=True)
class ControlTranslation:
    capability: str
    precision: TranslationPrecision
    strength: FilterStrength
    provider_control: str | None = None
    provider_value: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.precision, TranslationPrecision):
            raise TypeError("control precision must be closed")
        if not isinstance(self.strength, FilterStrength):
            raise TypeError("control strength must be closed")
        capabilities = {
            "none",
            "relative_only",
            "date_range",
            "relative_and_date_range",
            "query_qualifier",
        }
        controls = {
            "freshness",
            "start_date/end_date",
            "startPublishedDate/endPublishedDate",
            "fromDate/toDate",
            "advanced_settings.source_policy.after_date",
            "time_period_min/time_period_max",
            "time_range",
            "timelimit",
            "time_period",
            "query_qualifier",
        }
        if self.capability not in capabilities:
            raise ValueError("unknown provider control capability")
        if self.provider_control is not None and self.provider_control not in controls:
            raise ValueError("unknown provider control name")
        if self.provider_value is not None:
            value, truncated = _bounded_text(self.provider_value, MAX_REFERENCE)
            if not value or truncated:
                raise ValueError("provider control value must be bounded and private")
            object.__setattr__(self, "provider_value", value)


@dataclass(frozen=True, slots=True)
class ProviderRequestEvidence:
    effective_query_hash: str = ""
    provider_query_hash: str | None = None
    query_relation: QueryRelation = QueryRelation.UNKNOWN
    resolved_search_mode: str | None = None
    freshness_translation: ControlTranslation | None = None
    timeout_seconds: float | None = None
    attempt_id: str | None = None
    redirect_children: tuple[RedirectChildEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.query_relation, QueryRelation):
            raise TypeError("query relation must be closed")
        if self.freshness_translation is not None and not isinstance(
            self.freshness_translation, ControlTranslation
        ):
            raise TypeError("freshness translation must be typed")
        if self.effective_query_hash and not re.fullmatch(
            r"[0-9a-f]{64}", self.effective_query_hash
        ):
            object.__setattr__(self, "effective_query_hash", "")
        if self.provider_query_hash and not re.fullmatch(
            r"[0-9a-f]{64}", self.provider_query_hash
        ):
            object.__setattr__(self, "provider_query_hash", None)
        object.__setattr__(
            self, "resolved_search_mode", _bounded_label(self.resolved_search_mode)
        )
        object.__setattr__(self, "attempt_id", _bounded_reference(self.attempt_id))
        if self.timeout_seconds is not None:
            timeout = _finite_nonnegative(self.timeout_seconds)
            if timeout is None or timeout <= 0 or timeout > 300:
                raise ValueError("attempt timeout must be finite and bounded")
            object.__setattr__(self, "timeout_seconds", timeout)
        if not isinstance(self.redirect_children, tuple):
            raise TypeError("redirect children must be a tuple")
        children = self.redirect_children
        if len(children) > MAX_REDIRECTS:
            children = children[:MAX_REDIRECTS]
        if any(
            not isinstance(child, RedirectChildEvidence)
            or child.child_index != index
            or child.parent_attempt_id != self.attempt_id
            for index, child in enumerate(children, start=1)
        ):
            raise ValueError(
                "redirect children must be bounded ordered attempt children"
            )
        object.__setattr__(self, "redirect_children", children)


def _safe_warnings(values: Sequence[object]) -> tuple[str, ...]:
    output: list[str] = []
    for value in values:
        text, _ = _bounded_text(value, MAX_WARNING)
        if text:
            output.append(text)
        if len(output) == MAX_WARNINGS:
            break
    return tuple(output)


@dataclass(frozen=True, slots=True)
class UsageEvidence:
    count: float | None = None
    cost_usd: float | None = None
    transaction_id: str | None = None

    def __post_init__(self) -> None:
        limits = {"count": MAX_USAGE_COUNT, "cost_usd": MAX_COST_USD}
        for name, maximum in limits.items():
            value = getattr(self, name)
            if value is not None and _bounded_nonnegative(value, maximum) is None:
                raise ValueError(f"usage {name} must be finite and bounded")
        object.__setattr__(
            self, "count", _bounded_nonnegative(self.count, MAX_USAGE_COUNT)
        )
        object.__setattr__(
            self, "cost_usd", _bounded_nonnegative(self.cost_usd, MAX_COST_USD)
        )
        object.__setattr__(
            self, "transaction_id", _bounded_reference(self.transaction_id)
        )


@dataclass(frozen=True, slots=True)
class RateLimitEvidence:
    remaining: float | None = None
    reset_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.remaining is not None and _bounded_nonnegative(
            self.remaining, MAX_RATE_LIMIT_REMAINING
        ) is None:
            raise ValueError("rate remaining must be finite and bounded")
        object.__setattr__(
            self,
            "remaining",
            _bounded_nonnegative(self.remaining, MAX_RATE_LIMIT_REMAINING),
        )
        if self.reset_at is not None:
            if not isinstance(self.reset_at, datetime) or self.reset_at.tzinfo is None:
                raise ValueError("rate reset must be timezone-aware")
            object.__setattr__(self, "reset_at", self.reset_at.astimezone(timezone.utc))


@dataclass(frozen=True, slots=True)
class ProviderResponseEvidence:
    request_id: str | None = None
    session_id: str | None = None
    transaction_id: str | None = None
    warnings: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()
    usage_count: float | None = None
    cost_usd: float | None = None
    rate_limit_remaining: float | None = None
    rate_limit_reset: datetime | None = None
    http_status: int | None = None
    latency_ms: int = 0
    result_count: int = 0
    evidence_missing: bool = False
    charge_reported_invalid: bool = False
    usage: UsageEvidence | None = None
    rate_limit: RateLimitEvidence | None = None
    skipped: bool = False
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    egress: EgressType = EgressType.UNKNOWN
    machine: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.warnings, tuple) or not isinstance(
            self.suggestions, tuple
        ):
            raise TypeError("warnings and suggestions must be tuples")
        for name in (
            "evidence_missing",
            "charge_reported_invalid",
            "skipped",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be boolean")
        if not isinstance(self.observed_at, datetime):
            raise TypeError("provider observation time must be datetime")
        if not isinstance(self.egress, EgressType):
            raise TypeError("provider egress must be closed")
        if self.usage is not None and not isinstance(self.usage, UsageEvidence):
            raise TypeError("usage evidence must be typed")
        if self.rate_limit is not None and not isinstance(
            self.rate_limit, RateLimitEvidence
        ):
            raise TypeError("rate-limit evidence must be typed")
        if type(self.latency_ms) is not int or not 0 <= self.latency_ms <= 86_400_000:
            raise ValueError("latency must be a bounded integer")
        if type(self.result_count) is not int or not 0 <= self.result_count <= 10_000:
            raise ValueError("result count must be a bounded integer")
        object.__setattr__(self, "request_id", _bounded_reference(self.request_id))
        object.__setattr__(self, "session_id", _bounded_reference(self.session_id))
        object.__setattr__(
            self, "transaction_id", _bounded_reference(self.transaction_id)
        )
        object.__setattr__(self, "warnings", _safe_warnings(self.warnings))
        object.__setattr__(self, "suggestions", _safe_warnings(self.suggestions))
        numeric_limits = {
            "usage_count": MAX_USAGE_COUNT,
            "cost_usd": MAX_COST_USD,
            "rate_limit_remaining": MAX_RATE_LIMIT_REMAINING,
        }
        for name, maximum in numeric_limits.items():
            value = getattr(self, name)
            if value is not None and _bounded_nonnegative(value, maximum) is None:
                raise ValueError(f"response {name} must be finite and bounded")
        object.__setattr__(
            self,
            "usage_count",
            _bounded_nonnegative(self.usage_count, MAX_USAGE_COUNT),
        )
        object.__setattr__(
            self, "cost_usd", _bounded_nonnegative(self.cost_usd, MAX_COST_USD)
        )
        object.__setattr__(
            self,
            "rate_limit_remaining",
            _bounded_nonnegative(
                self.rate_limit_remaining, MAX_RATE_LIMIT_REMAINING
            ),
        )
        if self.rate_limit_reset is not None:
            object.__setattr__(
                self,
                "rate_limit_reset",
                _validated_rate_reset(
                    self.rate_limit_reset, observed_at=self.observed_at
                ),
            )
        usage = self.usage or UsageEvidence(
            self.usage_count, self.cost_usd, self.transaction_id
        )
        if any(
            value is not None
            for value in (usage.count, usage.cost_usd, usage.transaction_id)
        ):
            object.__setattr__(self, "usage", usage)
        rate_limit = self.rate_limit or RateLimitEvidence(
            self.rate_limit_remaining, self.rate_limit_reset
        )
        if rate_limit.reset_at is not None:
            _validated_rate_reset(rate_limit.reset_at, observed_at=self.observed_at)
        if rate_limit.remaining is not None or rate_limit.reset_at is not None:
            object.__setattr__(self, "rate_limit", rate_limit)
        if self.http_status is not None and (
            type(self.http_status) is not int or not 100 <= self.http_status <= 599
        ):
            raise ValueError("HTTP status must be in range 100..599")
        if self.observed_at.tzinfo is None:
            raise ValueError("provider observation time must be timezone-aware")
        object.__setattr__(
            self, "observed_at", self.observed_at.astimezone(timezone.utc)
        )
        object.__setattr__(self, "machine", _bounded_label(self.machine, MAX_REFERENCE))


@dataclass(frozen=True, slots=True)
class ProviderFailure(Exception):
    category: FailureCategory
    provider: ProviderName
    http_status: int | None = None
    provider_code: str | None = None
    retry_after_seconds: float | None = None
    rate_limit_reset: datetime | None = None
    request_id: str | None = None
    summary: str = ""
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not isinstance(self.category, FailureCategory):
            raise TypeError("failure category must be closed")
        if not isinstance(self.provider, ProviderName):
            raise TypeError("failure provider must be closed")
        if self.http_status is not None and (
            type(self.http_status) is not int or not 100 <= self.http_status <= 599
        ):
            raise ValueError("failure HTTP status must be in range 100..599")
        if self.rate_limit_reset is not None:
            object.__setattr__(
                self,
                "rate_limit_reset",
                _validated_rate_reset(
                    self.rate_limit_reset, observed_at=self.observed_at
                ),
            )
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ValueError("failure observation time must be timezone-aware")
        object.__setattr__(
            self, "observed_at", self.observed_at.astimezone(timezone.utc)
        )
        if (
            self.retry_after_seconds is not None
            and _bounded_nonnegative(
                self.retry_after_seconds, MAX_RETRY_AFTER_SECONDS
            )
            is None
        ):
            raise ValueError("retry-after must be finite and bounded")
        Exception.__init__(self, self.category.value)
        object.__setattr__(self, "provider_code", _bounded_label(self.provider_code))
        object.__setattr__(self, "request_id", _bounded_reference(self.request_id))
        summary, _ = _bounded_text(self.summary, MAX_WARNING)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(
            self,
            "retry_after_seconds",
            _bounded_nonnegative(
                self.retry_after_seconds, MAX_RETRY_AFTER_SECONDS
            ),
        )

    def safe_log_record(self) -> dict[str, object]:
        return {
            "provider": self.provider.value,
            "category": self.category.value,
            "http_status": self.http_status,
            "provider_code": self.provider_code,
            "retry_after_seconds": self.retry_after_seconds,
            "request_id": self.request_id,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class ResultObservation:
    provider: ProviderName
    provider_rank: int
    url: str
    title: str
    snippet: SnippetEvidence
    source_kind: EvidenceKind = EvidenceKind.WEB_PAGE
    provider_source_type: str | None = None
    upstream_engines: tuple[str, ...] = ()
    publication: PublicationEvidence | None = None
    native_score: NativeScoreEvidence | None = None
    provider_result_ref: str | None = None
    provider_position: int | None = None
    author: str | None = None
    language: str | None = None
    country: str | None = None
    section: str | None = None
    star_count: int | None = None
    fork_count: int | None = None
    topics: tuple[str, ...] = ()
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    egress: EgressType = EgressType.UNKNOWN
    machine: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, ProviderName):
            raise TypeError("observation provider must be closed")
        if not isinstance(self.snippet, SnippetEvidence):
            raise TypeError("observation snippet must be typed")
        if not isinstance(self.source_kind, EvidenceKind):
            raise TypeError("observation source kind must be closed")
        if not isinstance(self.egress, EgressType):
            raise TypeError("observation egress must be closed")
        if self.publication is not None and not isinstance(
            self.publication, PublicationEvidence
        ):
            raise TypeError("publication evidence must be typed")
        if self.native_score is not None and not isinstance(
            self.native_score, NativeScoreEvidence
        ):
            raise TypeError("native-score evidence must be typed")
        if not isinstance(self.upstream_engines, tuple) or not isinstance(
            self.topics, tuple
        ):
            raise TypeError("observation collections must be tuples")
        if not isinstance(self.observed_at, datetime):
            raise TypeError("observation time must be datetime")
        if type(self.provider_rank) is not int or self.provider_rank < 0:
            raise ValueError("provider rank must be a non-negative integer")
        url = _safe_url(self.url)
        if url is None:
            raise ValueError("observation URL must be bounded HTTP(S)")
        title, _ = _bounded_text(self.title, MAX_TITLE)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "title", title)
        object.__setattr__(
            self, "provider_source_type", _bounded_label(self.provider_source_type)
        )
        engines = tuple(
            item
            for item in (
                _bounded_label(value) for value in tuple(self.upstream_engines)[:16]
            )
            if item is not None
        )
        object.__setattr__(self, "upstream_engines", engines)
        object.__setattr__(
            self, "provider_result_ref", _bounded_reference(self.provider_result_ref)
        )
        if type(self.provider_position) is not int or self.provider_position < 0:
            object.__setattr__(self, "provider_position", None)
        for name, limit in (
            ("author", 256),
            ("language", MAX_LABEL),
            ("country", MAX_LABEL),
            ("section", MAX_LABEL),
        ):
            object.__setattr__(self, name, _bounded_label(getattr(self, name), limit))
        for name in ("star_count", "fork_count"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                object.__setattr__(self, name, None)
        object.__setattr__(
            self,
            "topics",
            tuple(
                topic
                for topic in (
                    _bounded_label(value) for value in tuple(self.topics)[:16]
                )
                if topic is not None
            ),
        )
        if self.observed_at.tzinfo is None:
            raise ValueError("result observation time must be timezone-aware")
        object.__setattr__(
            self, "observed_at", self.observed_at.astimezone(timezone.utc)
        )
        object.__setattr__(self, "machine", _bounded_label(self.machine, MAX_REFERENCE))


@dataclass(frozen=True, slots=True)
class ProviderSearchBatch:
    provider: ProviderName
    provider_contract_version: str
    request_evidence: ProviderRequestEvidence = field(
        default_factory=ProviderRequestEvidence
    )
    response_evidence: ProviderResponseEvidence = field(
        default_factory=ProviderResponseEvidence
    )
    observations: tuple[ResultObservation, ...] = ()
    failure: ProviderFailure | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, ProviderName):
            raise TypeError("batch provider must be closed")
        if not isinstance(self.request_evidence, ProviderRequestEvidence):
            raise TypeError("batch request evidence must be typed")
        if not isinstance(self.response_evidence, ProviderResponseEvidence):
            raise TypeError("batch response evidence must be typed")
        if self.failure is not None and not isinstance(self.failure, ProviderFailure):
            raise TypeError("batch failure must be typed")
        if self.failure is not None and self.failure.provider is not self.provider:
            raise ValueError("batch failure provider must match batch provider")
        version = _bounded_label(self.provider_contract_version)
        if version is None:
            raise ValueError("provider contract version must be bounded")
        object.__setattr__(self, "provider_contract_version", version)
        if not isinstance(self.observations, tuple):
            raise TypeError("batch observations must be a tuple")
        observations = self.observations
        if any(not isinstance(item, ResultObservation) for item in observations):
            raise TypeError("batch observations must be typed")
        if any(item.provider is not self.provider for item in observations):
            raise ValueError("observation provider must match batch provider")
        expected = list(range(len(observations)))
        ranks = [item.provider_rank for item in observations]
        if ranks != expected or any(
            item.provider is not self.provider for item in observations
        ):
            raise ValueError("provider ranks must be unique zero-based returned order")
        positions = [
            item.provider_position
            for item in observations
            if item.provider_position is not None
        ]
        duplicate_positions = {
            position for position in positions if positions.count(position) > 1
        }
        if duplicate_positions:
            observations = tuple(
                replace(item, provider_position=None)
                if item.provider_position in duplicate_positions
                else item
                for item in observations
            )
        object.__setattr__(self, "observations", observations)

    @property
    def results(self) -> list[SearchResult]:
        results: list[SearchResult] = []
        for item in self.observations:
            metadata: dict[str, object] = {}
            if item.star_count is not None:
                metadata["stars"] = item.star_count
            if item.fork_count is not None:
                metadata["forks"] = item.fork_count
            if item.topics:
                metadata["topics"] = list(item.topics)
            if item.language:
                metadata["language"] = item.language
            if item.provider_result_ref:
                metadata["id"] = item.provider_result_ref
            metadata["egress"] = item.egress.value
            metadata["observed_at"] = item.observed_at.isoformat()
            if item.machine:
                metadata["machine"] = item.machine
            results.append(
                SearchResult(
                    url=item.url,
                    title=item.title,
                    snippet=item.snippet.primary_text,
                    domain=urlsplit(item.url).hostname or "",
                    provider=item.provider,
                    score=item.native_score.value if item.native_score else 0.0,
                    raw_rank=item.provider_rank,
                    metadata=metadata,
                )
            )
        return results

    @property
    def trace(self) -> ProviderTrace:
        status = (
            "skipped"
            if self.response_evidence.skipped
            else (
                "success"
                if self.failure is None
                else (
                    "empty"
                    if self.failure.category is FailureCategory.EMPTY
                    else "error"
                )
            )
        )
        credit_info: dict[str, object] = {}
        if self.response_evidence.cost_usd is not None:
            credit_info["cost_usd"] = self.response_evidence.cost_usd
        if self.response_evidence.usage_count is not None:
            credit_info["usage_count"] = self.response_evidence.usage_count
        if self.response_evidence.transaction_id is not None:
            credit_info["transaction_id"] = self.response_evidence.transaction_id
        return ProviderTrace(
            provider=self.provider,
            status=status,
            results_count=len(self.observations),
            latency_ms=self.response_evidence.latency_ms,
            http_status=self.response_evidence.http_status,
            error=(
                self.failure.summary
                if self.failure
                else (
                    "provider returned an invalid charge; reservation left uncertain"
                    if self.response_evidence.charge_reported_invalid
                    else None
                )
            ),
            credit_info=credit_info or None,
            egress=self.response_evidence.egress.value,
        )

    def safe_log_record(self) -> dict[str, object]:
        return {
            "provider": self.provider.value,
            "provider_contract_version": self.provider_contract_version,
            "result_count": len(self.observations),
            "request_id": self.response_evidence.request_id,
            "warnings": self.response_evidence.warnings,
            "usage_count": self.response_evidence.usage_count,
            "cost_usd": self.response_evidence.cost_usd,
            "failure": self.failure.safe_log_record() if self.failure else None,
            "results": [
                {
                    "rank": item.provider_rank,
                    "url": item.url,
                    "title": item.title,
                    "snippet": item.snippet.primary_text,
                    "source_kind": item.source_kind.value,
                }
                for item in self.observations
            ],
        }


class LegacyProviderBatchAdapter:
    """Temporary migration reader for the old provider tuple."""

    @classmethod
    def from_legacy(
        cls, legacy: tuple[list[SearchResult], ProviderTrace]
    ) -> ProviderSearchBatch:
        results, trace = legacy
        credit_info = trace.credit_info or {}
        reported_cost = credit_info.get("cost_usd")
        cost = _finite_nonnegative(reported_cost)
        charge_invalid = "cost_usd" in credit_info and cost is None
        raw_egress = trace.egress or "local"
        try:
            egress = EgressType(raw_egress)
        except ValueError:
            egress = EgressType.UNKNOWN
        observations: list[ResultObservation] = []
        for result in results:
            provider = result.provider or trace.provider
            try:
                observations.append(
                    ResultObservation(
                        provider=provider,
                        provider_rank=len(observations),
                        url=result.url,
                        title=result.title,
                        snippet=SnippetEvidence(
                            result.snippet,
                            (
                                SnippetKind.PROVIDER_SNIPPET
                                if result.snippet
                                else SnippetKind.EMPTY
                            ),
                        ),
                        native_score=NativeScoreEvidence.from_value(
                            result.score,
                            semantics=NativeScoreSemantics.UNKNOWN,
                        ),
                        egress=egress,
                    )
                )
            except ValueError:
                continue
        failure = None
        if trace.status != "success":
            category = (
                FailureCategory.EMPTY
                if trace.status == "empty"
                else FailureCategory.PROVIDER_UNAVAILABLE
            )
            failure = ProviderFailure(
                category=category,
                provider=trace.provider,
                summary=trace.error or category.value,
            )
        return ProviderSearchBatch(
            provider=trace.provider,
            provider_contract_version="legacy-v1",
            response_evidence=ProviderResponseEvidence(
                latency_ms=trace.latency_ms,
                result_count=len(observations),
                evidence_missing=True,
                cost_usd=cost,
                usage_count=credit_info.get("usage_count"),
                transaction_id=(
                    credit_info.get("transaction_id") or credit_info.get("tx_id")
                ),
                egress=egress,
                charge_reported_invalid=charge_invalid,
                skipped=trace.status == "skipped",
            ),
            observations=tuple(observations),
            failure=failure,
        )


def classify_http_failure(
    provider: ProviderName,
    status: int,
    *,
    provider_code: str | None = None,
    request_id: str | None = None,
    summary: str = "",
    retry_after_seconds: float | None = None,
    rate_limit_reset: datetime | None = None,
    observed_at: datetime | None = None,
    raw_body: object = None,
    request_url: object = None,
    headers: object = None,
) -> ProviderFailure:
    """Classify allowlisted HTTP evidence; raw inputs are intentionally ignored."""
    del raw_body, request_url, headers
    if status == 401:
        category = FailureCategory.AUTHENTICATION_REJECTED
    elif status == 402:
        category = FailureCategory.BALANCE_EXHAUSTED
    elif provider is ProviderName.WOLFRAM and status == 403:
        category = FailureCategory.AUTHENTICATION_REJECTED
    elif status == 403:
        category = FailureCategory.POLICY_REJECTED
    elif status in {408, 504}:
        category = FailureCategory.TIMEOUT
    elif status in {400, 409, 422}:
        category = FailureCategory.INVALID_REQUEST
    elif status == 429:
        category = FailureCategory.RATE_LIMITED
    else:
        category = FailureCategory.PROVIDER_UNAVAILABLE
    return ProviderFailure(
        category=category,
        provider=provider,
        http_status=status,
        provider_code=provider_code,
        retry_after_seconds=retry_after_seconds,
        rate_limit_reset=rate_limit_reset,
        request_id=request_id,
        summary=summary or category.value.replace("_", " "),
        observed_at=observed_at or datetime.now(timezone.utc),
    )


def attempt_timeout_seconds(
    *,
    configured_timeout: float,
    provider_phase_deadline: float,
    monotonic,
) -> float:
    remaining = float(provider_phase_deadline) - float(monotonic())
    if remaining <= 0:
        raise TimeoutError("provider phase deadline reached")
    if not math.isfinite(configured_timeout) or configured_timeout <= 0:
        raise ValueError("configured timeout must be finite and positive")
    return min(float(configured_timeout), remaining)


T = TypeVar("T")


async def run_with_attempt_deadline(
    operation: Awaitable[T],
    *,
    configured_timeout: float,
    provider_phase_deadline: float,
    monotonic,
) -> T:
    timeout = attempt_timeout_seconds(
        configured_timeout=configured_timeout,
        provider_phase_deadline=provider_phase_deadline,
        monotonic=monotonic,
    )
    try:
        return await asyncio.wait_for(operation, timeout=timeout)
    except asyncio.TimeoutError as error:
        raise TimeoutError("provider phase deadline reached") from error


@dataclass(frozen=True, slots=True)
class RedirectChildEvidence:
    parent_attempt_id: str
    child_index: int
    source_origin: str
    destination_origin: str
    cross_origin: bool
    credentials_stripped: bool
    timeout_seconds: float

    def __post_init__(self) -> None:
        if (
            type(self.cross_origin) is not bool
            or type(self.credentials_stripped) is not bool
        ):
            raise TypeError("redirect flags must be boolean")
        parent = _bounded_reference(self.parent_attempt_id)
        if parent is None:
            raise ValueError("redirect parent attempt ID must be bounded")
        if (
            type(self.child_index) is not int
            or not 1 <= self.child_index <= MAX_REDIRECTS
        ):
            raise ValueError("redirect child index exceeds the trace bound")
        source = _bounded_label(self.source_origin, MAX_REFERENCE)
        destination = _bounded_label(self.destination_origin, MAX_REFERENCE)
        timeout = _finite_nonnegative(self.timeout_seconds)
        if source is None or destination is None or timeout is None or timeout <= 0:
            raise ValueError("redirect child evidence must be bounded")
        object.__setattr__(self, "parent_attempt_id", parent)
        object.__setattr__(self, "source_origin", source)
        object.__setattr__(self, "destination_origin", destination)
        object.__setattr__(self, "timeout_seconds", timeout)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    port = parts.port
    default_port = 443 if parts.scheme == "https" else 80
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{parts.scheme}://{parts.hostname}{suffix}"


@dataclass(frozen=True, slots=True)
class RedirectRequest:
    url: str
    headers: dict[str, str]
    redirect_count: int
    child_evidence: RedirectChildEvidence


def safe_redirect_request(
    *,
    source_url: str,
    destination_url: str,
    headers: Mapping[str, str],
    redirect_count: int,
    parent_attempt_id: str = "provider-attempt",
    timeout_seconds: float = 1.0,
    max_redirects: int = MAX_REDIRECTS,
) -> RedirectRequest:
    if redirect_count > max_redirects:
        raise ProviderFailure(
            category=FailureCategory.POLICY_REJECTED,
            provider=ProviderName.YAHOO,
            summary="redirect trace limit exceeded",
        )
    destination = _safe_url(destination_url)
    if destination is None:
        raise ProviderFailure(
            category=FailureCategory.POLICY_REJECTED,
            provider=ProviderName.YAHOO,
            summary="unsafe redirect target",
        )
    source = urlsplit(source_url)
    target = urlsplit(destination)
    cross_origin = (
        source.scheme.lower(),
        source.hostname,
        source.port,
    ) != (
        target.scheme.lower(),
        target.hostname,
        target.port,
    )
    removed_credentials = any(_SECRET_KEY.search(key) for key in headers)
    safe_headers = {
        key: value
        for key, value in headers.items()
        if not _SECRET_KEY.search(key) and (not cross_origin or key.lower() != "origin")
    }
    return RedirectRequest(
        destination,
        safe_headers,
        redirect_count,
        RedirectChildEvidence(
            parent_attempt_id=parent_attempt_id,
            child_index=redirect_count,
            source_origin=_origin(source_url),
            destination_origin=_origin(destination),
            cross_origin=cross_origin,
            credentials_stripped=cross_origin and removed_credentials,
            timeout_seconds=timeout_seconds,
        ),
    )


_CONTRACT_VERSION = {
    provider: "2026-07-27-v1"
    for provider in ProviderName
    if provider is not ProviderName.CACHE
}


def with_response_timing(
    batch: ProviderSearchBatch,
    *,
    latency_ms: int,
    rate_limit_remaining: object = None,
    rate_limit_reset: datetime | None = None,
) -> ProviderSearchBatch:
    """Attach allowlisted transport evidence to an already normalized batch."""
    response = replace(
        batch.response_evidence,
        latency_ms=latency_ms,
        rate_limit_remaining=(
            rate_limit_remaining
            if rate_limit_remaining is not None
            else batch.response_evidence.rate_limit_remaining
        ),
        rate_limit_reset=rate_limit_reset or batch.response_evidence.rate_limit_reset,
    )
    return replace(batch, response_evidence=response)


def with_trusted_provenance(
    batch: ProviderSearchBatch,
    *,
    egress: EgressType,
    machine: str | None,
) -> ProviderSearchBatch:
    """Stamp trusted route provenance without overriding adapter route evidence."""
    if egress is EgressType.UNKNOWN and machine is None:
        return batch
    selected_egress = (
        batch.response_evidence.egress
        if batch.response_evidence.egress is not EgressType.UNKNOWN
        else egress
    )
    selected_machine = batch.response_evidence.machine or machine
    response = replace(
        batch.response_evidence,
        egress=selected_egress,
        machine=selected_machine,
    )
    observations = tuple(
        replace(
            item,
            egress=(
                item.egress
                if item.egress is not EgressType.UNKNOWN
                else selected_egress
            ),
            machine=item.machine or selected_machine,
        )
        for item in batch.observations
    )
    return replace(batch, response_evidence=response, observations=observations)


def failure_batch(
    provider: ProviderName,
    error: BaseException,
    *,
    latency_ms: int = 0,
    request_evidence: ProviderRequestEvidence | None = None,
    observed_status: int | None = None,
) -> ProviderSearchBatch:
    """Convert a transport/parser exception into bounded private evidence."""
    if isinstance(error, ProviderFailure):
        failure = (
            replace(error, http_status=observed_status)
            if error.http_status is None and observed_status is not None
            else error
        )
        return ProviderSearchBatch(
            provider=provider,
            provider_contract_version=_CONTRACT_VERSION[provider],
            request_evidence=request_evidence or ProviderRequestEvidence(),
            response_evidence=ProviderResponseEvidence(
                latency_ms=latency_ms,
                http_status=failure.http_status,
                request_id=failure.request_id,
                rate_limit_reset=failure.rate_limit_reset,
                observed_at=failure.observed_at,
            ),
            failure=failure,
        )
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        failure = classify_http_failure(
            provider,
            status,
            summary=f"{provider.value} HTTP request failed",
        )
    elif observed_status is not None:
        failure = ProviderFailure(
            FailureCategory.PARSE_ERROR,
            provider,
            http_status=observed_status,
            summary=f"{provider.value} response parsing failed ({type(error).__name__})",
        )
    elif (
        isinstance(error, (TimeoutError, asyncio.TimeoutError))
        or "timeout" in type(error).__name__.lower()
    ):
        failure = ProviderFailure(
            FailureCategory.TIMEOUT,
            provider,
            summary=f"{provider.value} request timed out",
        )
    else:
        failure = ProviderFailure(
            FailureCategory.PROVIDER_UNAVAILABLE,
            provider,
            summary=f"{provider.value} request failed ({type(error).__name__})",
        )
    return ProviderSearchBatch(
        provider=provider,
        provider_contract_version=_CONTRACT_VERSION[provider],
        request_evidence=request_evidence or ProviderRequestEvidence(),
        response_evidence=ProviderResponseEvidence(
            latency_ms=latency_ms,
            http_status=failure.http_status,
            request_id=failure.request_id,
            rate_limit_reset=failure.rate_limit_reset,
            observed_at=failure.observed_at,
        ),
        failure=failure,
    )


def skipped_batch(provider: ProviderName, summary: str) -> ProviderSearchBatch:
    return ProviderSearchBatch(
        provider=provider,
        provider_contract_version=_CONTRACT_VERSION[provider],
        response_evidence=ProviderResponseEvidence(skipped=True),
        failure=ProviderFailure(
            FailureCategory.POLICY_REJECTED,
            provider,
            summary=summary,
        ),
    )
