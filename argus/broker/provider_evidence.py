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
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Mapping, Sequence, TypeVar
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

_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|appid|authorization|cookie|credential|password|"
    r"secret|signature|signed|token|x-amz-|x-goog-)",
    re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"(?:authorization\s*:|bearer\s+|cookie\s*:|set-cookie\s*:|"
    r"(?:api[_-]?key|appid|credential|password|secret|signature|token)\s*[=:]|"
    r"/(?:Users|home)/[^\s]+|[A-Z]:\\\\Users\\\\)",
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


def _bounded_text(value: object, limit: int) -> tuple[str, bool]:
    if value is None:
        return "", False
    text = str(value)
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
    if isinstance(value, bool):
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if math.isfinite(normalized) and normalized >= 0 else None


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


@dataclass(frozen=True, slots=True)
class ProviderRequestEvidence:
    effective_query_hash: str = ""
    provider_query_hash: str | None = None
    query_relation: QueryRelation = QueryRelation.UNKNOWN
    resolved_search_mode: str | None = None
    freshness_translation: ControlTranslation | None = None
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
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
    latency_ms: int = 0
    result_count: int = 0
    evidence_missing: bool = False
    skipped: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _bounded_reference(self.request_id))
        object.__setattr__(self, "session_id", _bounded_reference(self.session_id))
        object.__setattr__(
            self, "transaction_id", _bounded_reference(self.transaction_id)
        )
        object.__setattr__(self, "warnings", _safe_warnings(self.warnings))
        object.__setattr__(self, "suggestions", _safe_warnings(self.suggestions))
        object.__setattr__(self, "usage_count", _finite_nonnegative(self.usage_count))
        object.__setattr__(self, "cost_usd", _finite_nonnegative(self.cost_usd))
        object.__setattr__(
            self,
            "rate_limit_remaining",
            _finite_nonnegative(self.rate_limit_remaining),
        )
        if self.rate_limit_reset is not None and (
            self.rate_limit_reset.tzinfo is None
        ):
            object.__setattr__(self, "rate_limit_reset", None)
        object.__setattr__(self, "latency_ms", max(0, int(self.latency_ms)))
        object.__setattr__(self, "result_count", max(0, int(self.result_count)))


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

    def __post_init__(self) -> None:
        Exception.__init__(self, self.category.value)
        object.__setattr__(self, "provider_code", _bounded_label(self.provider_code))
        object.__setattr__(self, "request_id", _bounded_reference(self.request_id))
        summary, _ = _bounded_text(self.summary, MAX_WARNING)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(
            self, "retry_after_seconds", _finite_nonnegative(self.retry_after_seconds)
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

    def __post_init__(self) -> None:
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
            object.__setattr__(
                self, name, _bounded_label(getattr(self, name), limit)
            )
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
                    _bounded_label(value)
                    for value in tuple(self.topics)[:16]
                )
                if topic is not None
            ),
        )
        if self.publication is not None and not isinstance(
            self.publication, PublicationEvidence
        ):
            object.__setattr__(self, "publication", None)
        if self.native_score is not None and not isinstance(
            self.native_score, NativeScoreEvidence
        ):
            object.__setattr__(self, "native_score", None)


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
        version = _bounded_label(self.provider_contract_version)
        if version is None:
            raise ValueError("provider contract version must be bounded")
        object.__setattr__(self, "provider_contract_version", version)
        observations = tuple(self.observations)
        expected = list(range(len(observations)))
        ranks = [item.provider_rank for item in observations]
        if ranks != expected or any(item.provider is not self.provider for item in observations):
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
            results.append(SearchResult(
                url=item.url,
                title=item.title,
                snippet=item.snippet.primary_text,
                domain=urlsplit(item.url).hostname or "",
                provider=item.provider,
                score=item.native_score.value if item.native_score else 0.0,
                raw_rank=item.provider_rank,
                metadata=metadata,
            ))
        return results

    @property
    def trace(self) -> ProviderTrace:
        status = "skipped" if self.response_evidence.skipped else (
            "success"
            if self.failure is None
            else ("empty" if self.failure.category is FailureCategory.EMPTY else "error")
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
            error=self.failure.summary if self.failure else None,
            credit_info=credit_info or None,
        )

    def __iter__(self):
        """Temporary tuple projection for callers removed in the next slice."""
        yield self.results
        yield self.trace

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
class RedirectRequest:
    url: str
    headers: dict[str, str]
    redirect_count: int


def safe_redirect_request(
    *,
    source_url: str,
    destination_url: str,
    headers: Mapping[str, str],
    redirect_count: int,
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
    safe_headers = {
        key: value
        for key, value in headers.items()
        if not _SECRET_KEY.search(key) and (not cross_origin or key.lower() != "origin")
    }
    return RedirectRequest(destination, safe_headers, redirect_count)


_CONTRACT_VERSION = {
    provider: "2026-07-27-v1"
    for provider in ProviderName
    if provider is not ProviderName.CACHE
}


def _mapping(data: object) -> Mapping[str, Any] | None:
    return data if isinstance(data, Mapping) else None


def _sequence(value: object) -> list[Mapping[str, Any]] | None:
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, Mapping)]


def _result_rows(
    provider: ProviderName, data: Mapping[str, Any]
) -> tuple[list[Mapping[str, Any]] | None, bool]:
    if provider is ProviderName.BRAVE:
        web = _mapping(data.get("web"))
        return (_sequence(web.get("results")) if web else None, web is not None)
    if provider is ProviderName.GITHUB:
        return _sequence(data.get("items")), "items" in data
    if provider is ProviderName.SERPER:
        return _sequence(data.get("organic")), "organic" in data
    if provider is ProviderName.YOU:
        results = _mapping(data.get("results"))
        return (
            _sequence(results.get("web")) if results else None,
            results is not None and "web" in results,
        )
    if provider is ProviderName.SEARCHAPI:
        key = "organic_results" if "organic_results" in data else "organic"
        return _sequence(data.get(key)), key in data
    if provider is ProviderName.WOLFRAM:
        answer = data.get("answer")
        if isinstance(answer, str):
            return (
                [
                    {
                        "url": data.get("query_url")
                        or "https://www.wolframalpha.com/",
                        "title": data.get("title") or "WolframAlpha",
                        "text": answer,
                    }
                ],
                True,
            )
        return ([], data.get("empty") is True)
    key = "results"
    return _sequence(data.get(key)), key in data


def _row_fields(
    provider: ProviderName, item: Mapping[str, Any]
) -> tuple[object, object, object, SnippetKind]:
    if provider is ProviderName.DUCKDUCKGO:
        return item.get("href"), item.get("title"), item.get("body"), SnippetKind.PROVIDER_SNIPPET
    if provider is ProviderName.GITHUB:
        return item.get("html_url"), item.get("full_name") or item.get("name"), item.get("description"), SnippetKind.PROVIDER_DESCRIPTION
    if provider is ProviderName.LINKUP:
        return item.get("url"), item.get("name"), item.get("content"), SnippetKind.PROVIDER_TEXT_EXCERPT
    if provider is ProviderName.PARALLEL:
        excerpts = item.get("excerpts")
        if isinstance(excerpts, list):
            snippet = " ".join(str(value) for value in excerpts[:3])
        else:
            snippet = item.get("excerpt") or item.get("snippet")
        return item.get("url"), item.get("title"), snippet, SnippetKind.PROVIDER_TEXT_EXCERPT
    if provider is ProviderName.SERPER:
        return item.get("link"), item.get("title"), item.get("snippet"), SnippetKind.PROVIDER_SNIPPET
    if provider is ProviderName.SEARCHAPI:
        return item.get("link") or item.get("url"), item.get("title"), item.get("snippet") or item.get("description"), SnippetKind.PROVIDER_SNIPPET
    if provider is ProviderName.SEARXNG:
        return item.get("url"), item.get("title"), item.get("content"), SnippetKind.PROVIDER_SNIPPET
    if provider is ProviderName.TAVILY:
        return item.get("url"), item.get("title"), item.get("content"), SnippetKind.PROVIDER_TEXT_EXCERPT
    if provider is ProviderName.EXA:
        highlights = item.get("highlights")
        snippet = (
            " ".join(str(value) for value in highlights[:3])
            if isinstance(highlights, list) and highlights
            else item.get("text")
        )
        return item.get("url"), item.get("title"), snippet, SnippetKind.PROVIDER_HIGHLIGHT
    if provider is ProviderName.VALYU:
        return item.get("url"), item.get("title"), item.get("description") or item.get("content"), SnippetKind.PROVIDER_DESCRIPTION
    if provider is ProviderName.BRAVE:
        return item.get("url"), item.get("title"), item.get("description"), SnippetKind.PROVIDER_DESCRIPTION
    if provider is ProviderName.YOU:
        snippets = item.get("snippets")
        snippet = snippets[0] if isinstance(snippets, list) and snippets else item.get("description")
        return item.get("url"), item.get("title"), snippet, SnippetKind.PROVIDER_SNIPPET
    if provider is ProviderName.WOLFRAM:
        return item.get("url"), item.get("title"), item.get("text"), SnippetKind.PROVIDER_TEXT_EXCERPT
    return item.get("url"), item.get("title"), item.get("snippet"), SnippetKind.PROVIDER_SNIPPET


def _publication(
    provider: ProviderName, item: Mapping[str, Any]
) -> PublicationEvidence | None:
    candidates: tuple[str, ...]
    if provider is ProviderName.EXA:
        candidates = ("publishedDate", "published_date")
    elif provider is ProviderName.PARALLEL:
        candidates = ("publish_date",)
    elif provider is ProviderName.VALYU:
        candidates = ("publication_date",)
    elif provider is ProviderName.TAVILY:
        candidates = ("published_date",)
    elif provider is ProviderName.SEARXNG:
        candidates = ("publishedDate",)
    else:
        candidates = ()
    for field_name in candidates:
        if field_name in item:
            confidence = (
                ContractConfidence.FIXTURE_BACKED
                if field_name == "published_date" and provider is ProviderName.EXA
                else ContractConfidence.OFFICIAL_CONTRACT
            )
            return PublicationEvidence.from_raw(
                item[field_name],
                raw_field_name=field_name,
                confidence=confidence,
                semantic_contract_ref=f"{provider.value}-search-contract",
            )
    return None


def _native_score(
    provider: ProviderName, item: Mapping[str, Any]
) -> NativeScoreEvidence | None:
    if provider is ProviderName.SEARXNG:
        value = item.get("score")
        semantics = NativeScoreSemantics.PROVIDER_RANK_SCORE
    elif provider is ProviderName.TAVILY:
        value = item.get("score")
        semantics = NativeScoreSemantics.RELEVANCE
    elif provider is ProviderName.VALYU:
        value = item.get("relevance_score")
        semantics = NativeScoreSemantics.RELEVANCE
    else:
        return None
    return NativeScoreEvidence.from_value(
        value,
        semantics=semantics,
        confidence=ContractConfidence.OFFICIAL_CONTRACT,
    )


def _source(provider: ProviderName, item: Mapping[str, Any]) -> EvidenceKind:
    if provider is ProviderName.GITHUB:
        return EvidenceKind.REPOSITORY
    if provider is ProviderName.WOLFRAM:
        return EvidenceKind.COMPUTED_ANSWER
    if provider is ProviderName.YOU and item.get("_section") == "news":
        return EvidenceKind.NEWS
    source = item.get("source_type") or item.get("type")
    mapping = {
        "web": EvidenceKind.WEB_PAGE,
        "news": EvidenceKind.NEWS,
        "paper": EvidenceKind.PAPER,
        "proprietary": EvidenceKind.PROPRIETARY,
    }
    if source is not None:
        return mapping.get(str(source).lower(), EvidenceKind.UNKNOWN)
    return EvidenceKind.WEB_PAGE


def _response_evidence(
    provider: ProviderName, data: Mapping[str, Any], count: int
) -> ProviderResponseEvidence:
    metadata = _mapping(data.get("metadata")) or {}
    usage = _mapping(data.get("usage")) or {}
    search_metadata = _mapping(data.get("search_metadata")) or {}
    warning_values = data.get("warnings")
    if not isinstance(warning_values, list):
        warning_values = [data.get("error")] if data.get("success") is True and data.get("error") else []
    request_id = (
        data.get("requestId")
        or data.get("request_id")
        or data.get("search_id")
        or metadata.get("search_uuid")
        or search_metadata.get("id")
    )
    session_id = data.get("session_id")
    transaction_id = data.get("tx_id")
    usage_count = usage.get("credits")
    if usage_count is None:
        usage_count = usage.get("total_tokens")
    cost = data.get("costDollars")
    if isinstance(cost, Mapping):
        cost = cost.get("total")
    if cost is None:
        cost = data.get("total_deduction_dollars")
    return ProviderResponseEvidence(
        request_id=request_id,
        session_id=session_id,
        transaction_id=transaction_id,
        warnings=tuple(warning_values),
        usage_count=usage_count,
        cost_usd=cost,
        result_count=count,
    )


def normalize_provider_response(
    provider: ProviderName,
    payload: object,
    *,
    max_results: int,
    request_evidence: ProviderRequestEvidence | None = None,
) -> ProviderSearchBatch:
    """Normalize one captured provider payload without retaining native fields."""
    data = _mapping(payload)
    if data is None or isinstance(max_results, bool) or max_results <= 0:
        failure = ProviderFailure(
            FailureCategory.PARSE_ERROR, provider, summary="invalid provider response shape"
        )
        return ProviderSearchBatch(
            provider,
            _CONTRACT_VERSION[provider],
            request_evidence or ProviderRequestEvidence(),
            ProviderResponseEvidence(),
            (),
            failure,
        )
    rows, recognized = _result_rows(provider, data)
    if rows is None or not recognized:
        failure = ProviderFailure(
            FailureCategory.PARSE_ERROR,
            provider,
            summary="provider success response did not match contract",
        )
        return ProviderSearchBatch(
            provider,
            _CONTRACT_VERSION[provider],
            request_evidence or ProviderRequestEvidence(),
            ProviderResponseEvidence(),
            (),
            failure,
        )
    observations: list[ResultObservation] = []
    for item in rows:
        if len(observations) >= max_results:
            break
        url, title, snippet, snippet_kind = _row_fields(provider, item)
        try:
            engines = item.get("engines")
            if not isinstance(engines, list):
                engines = [item.get("engine")] if item.get("engine") else []
            highlights = item.get("highlights")
            observation = ResultObservation(
                provider=provider,
                provider_rank=len(observations),
                url=url,
                title=title or "",
                snippet=SnippetEvidence(
                    snippet or "",
                    snippet_kind if snippet else SnippetKind.EMPTY,
                    tuple(highlights) if isinstance(highlights, list) else (),
                ),
                source_kind=_source(provider, item),
                provider_source_type=item.get("source_type") or item.get("type"),
                upstream_engines=tuple(engines),
                publication=_publication(provider, item),
                native_score=_native_score(provider, item),
                provider_result_ref=item.get("id"),
                provider_position=item.get("position"),
                author=item.get("author"),
                language=item.get("language"),
                section=item.get("_section"),
                star_count=item.get("stargazers_count"),
                fork_count=item.get("forks_count"),
                topics=(
                    tuple(item.get("topics"))
                    if isinstance(item.get("topics"), list)
                    else ()
                ),
            )
        except (TypeError, ValueError):
            continue
        observations.append(observation)
    response = _response_evidence(provider, data, len(observations))
    if rows and not observations:
        failure = ProviderFailure(
            FailureCategory.PARSE_ERROR,
            provider,
            summary="all provider result rows were structurally invalid",
        )
    elif not rows:
        failure = ProviderFailure(
            FailureCategory.EMPTY, provider, summary="valid empty provider response"
        )
    else:
        failure = None
    return ProviderSearchBatch(
        provider=provider,
        provider_contract_version=_CONTRACT_VERSION[provider],
        request_evidence=request_evidence or ProviderRequestEvidence(),
        response_evidence=response,
        observations=tuple(observations),
        failure=failure,
    )


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


def failure_batch(
    provider: ProviderName,
    error: BaseException,
    *,
    latency_ms: int = 0,
) -> ProviderSearchBatch:
    """Convert a transport/parser exception into bounded private evidence."""
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        error_summary, _ = _bounded_text(str(error), MAX_WARNING)
        if (
            provider is ProviderName.GITHUB
            and status == 403
            and "rate limit" in error_summary.lower()
        ):
            failure = ProviderFailure(
                FailureCategory.RATE_LIMITED,
                provider,
                http_status=status,
                summary="rate limited",
            )
        else:
            failure = classify_http_failure(
                provider,
                status,
                summary=f"{provider.value} HTTP request failed",
            )
    elif isinstance(error, (TimeoutError, asyncio.TimeoutError)) or "timeout" in type(
        error
    ).__name__.lower():
        failure = ProviderFailure(
            FailureCategory.TIMEOUT,
            provider,
            summary=f"{provider.value} request timed out",
        )
    else:
        safe_summary, _ = _bounded_text(str(error), MAX_WARNING)
        failure = ProviderFailure(
            FailureCategory.PROVIDER_UNAVAILABLE,
            provider,
            summary=safe_summary or f"{provider.value} request failed",
        )
    return ProviderSearchBatch(
        provider=provider,
        provider_contract_version=_CONTRACT_VERSION[provider],
        response_evidence=ProviderResponseEvidence(latency_ms=latency_ms),
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
