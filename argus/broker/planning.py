"""Deterministic retrieval planning."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Callable

from argus.broker.budgets import PROVIDER_TIERS
from argus.broker.policies import resolve_routing, stable_tier_sort
from argus.contracts.outcomes import CanonicalOutcome
from argus.models import ProviderName, SearchMode, SearchQuery

_MAX_QUERY_BYTES = 16_384
_MAX_RAW_PROVIDERS = 32
_MAX_RAW_DOMAINS = 64
_MAX_RAW_DOMAIN_BYTES = 1_024
_MAX_DOMAIN_BYTES = 253
_MAX_VERSION_BYTES = 64
_MAX_DEADLINE_MS = 120_000
_DEFAULT_CACHE_AGE_SECONDS = 604_800
_PROVIDER_PHASE_RESERVE_MS = 5_000
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class FreshnessRelative(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class SafeSearch(str, Enum):
    UNSPECIFIED = "unspecified"
    MODERATE = "moderate"
    STRICT = "strict"
    OFF = "off"


class RevalidationMode(str, Enum):
    NORMAL = "normal"
    FORCE = "force"


class EgressPreference(str, Enum):
    DEFAULT = "default"
    PREFER_RESIDENTIAL = "prefer_residential"


@dataclass(frozen=True, slots=True)
class FreshnessWindow:
    requested_relative: FreshnessRelative | None = None
    start_date: date | None = None
    end_date: date | None = None
    max_cache_age_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class DomainConstraints:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalControls:
    freshness: FreshnessWindow = field(default_factory=FreshnessWindow)
    domains: DomainConstraints = field(default_factory=DomainConstraints)
    safe_search: SafeSearch = SafeSearch.UNSPECIFIED
    country: str | None = None
    language: str | None = None
    deadline_ms: int = 120_000
    revalidation: RevalidationMode = RevalidationMode.NORMAL


@dataclass(frozen=True, slots=True)
class ExecutionPolicySnapshot:
    effective_max_provider_tier: int = 3
    allowed_providers: tuple[ProviderName, ...] | None = None
    egress_preference: EgressPreference = EgressPreference.DEFAULT
    plan_schema_version: int = 1
    cache_identity_schema_version: int = 1
    query_normalization_version: str = "1"
    routing_policy_version: str = "1"
    spend_policy_version: str = "1"
    freshness_policy_version: str = "1"
    domain_policy_version: str = "1"
    ranking_policy_version: str = "1"
    result_normalization_version: str = "1"


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    plan_schema_version: int
    normalized_query: str
    intent: SearchMode
    result_limit: int
    explicit_providers: tuple[ProviderName, ...] | None
    freshness: FreshnessWindow
    domains: DomainConstraints
    safe_search: SafeSearch
    country: str | None
    language: str | None
    profile: str
    effective_max_provider_tier: int
    candidate_providers: tuple[ProviderName, ...]
    deadline_ms: int
    revalidation: RevalidationMode
    egress_preference: EgressPreference
    include_attribution: bool
    cache_identity_schema_version: int
    query_normalization_version: str
    routing_policy_version: str
    spend_policy_version: str
    freshness_policy_version: str
    domain_policy_version: str
    ranking_policy_version: str
    result_normalization_version: str
    plan_id: str
    cache_fingerprint: str

    @property
    def provider_phase_budget_ms(self) -> int:
        return max(0, self.deadline_ms - _PROVIDER_PHASE_RESERVE_MS)


class InvalidRetrievalPlan(ValueError):
    outcome = CanonicalOutcome.INVALID_REQUEST


def _invalid(message: str) -> InvalidRetrievalPlan:
    return InvalidRetrievalPlan(message)


def _utf8_size(value: str, name: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeError as error:
        raise _invalid(f"{name} is not valid UTF-8 text") from error


def _validate_raw_bounds(
    query: SearchQuery,
    controls: RetrievalControls,
    policy: ExecutionPolicySnapshot,
) -> None:
    if not isinstance(query.query, str):
        raise _invalid("query must be a string")
    if _utf8_size(query.query, "query") > _MAX_QUERY_BYTES:
        raise _invalid("query exceeds the UTF-8 byte limit")

    providers = query.providers
    if providers is not None:
        if not isinstance(providers, (list, tuple)):
            raise _invalid("explicit providers must be a bounded sequence")
        if len(providers) > _MAX_RAW_PROVIDERS:
            raise _invalid("explicit providers exceed the raw entry limit")

    domains = controls.domains
    if not isinstance(domains, DomainConstraints):
        raise _invalid("domains must be typed domain constraints")
    if not isinstance(domains.include, (list, tuple)) or not isinstance(
        domains.exclude, (list, tuple)
    ):
        raise _invalid("domain constraints must be bounded sequences")
    if len(domains.include) + len(domains.exclude) > _MAX_RAW_DOMAINS:
        raise _invalid("domain constraints exceed the raw entry limit")
    for domain in (*domains.include, *domains.exclude):
        if not isinstance(domain, str):
            raise _invalid("domain constraints must be strings")
        if _utf8_size(domain, "domain constraint") > _MAX_RAW_DOMAIN_BYTES:
            raise _invalid("domain constraint exceeds the raw UTF-8 byte limit")

    for name in (
        "query_normalization_version",
        "routing_policy_version",
        "spend_policy_version",
        "freshness_policy_version",
        "domain_policy_version",
        "ranking_policy_version",
        "result_normalization_version",
    ):
        value = getattr(policy, name)
        if not isinstance(value, str):
            raise _invalid(f"{name} must be printable bounded ASCII")
        try:
            size = len(value.encode("ascii"))
        except UnicodeError as error:
            raise _invalid(f"{name} must be printable bounded ASCII") from error
        if size > _MAX_VERSION_BYTES:
            raise _invalid(f"{name} exceeds the raw byte limit")


def _require_plain_int(
    name: str,
    value: object,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid(f"{name} must be an integer from {minimum} through {maximum}")
    return value


def _normalize_query(raw_query: object) -> str:
    if not isinstance(raw_query, str):
        raise _invalid("query must be a string")
    if len(raw_query.encode("utf-8")) > _MAX_QUERY_BYTES:
        raise _invalid("query exceeds the UTF-8 byte limit")
    normalized = unicodedata.normalize("NFC", raw_query)
    normalized = " ".join(normalized.split())
    if not normalized:
        raise _invalid("normalized query must not be empty")
    if len(normalized.encode("utf-8")) > _MAX_QUERY_BYTES:
        raise _invalid("normalized query exceeds the UTF-8 byte limit")
    return normalized


def _normalize_providers(
    providers: object,
) -> tuple[ProviderName, ...] | None:
    if providers is None:
        return None
    if not isinstance(providers, (list, tuple)):
        raise _invalid("explicit providers must be a bounded sequence")
    if len(providers) > _MAX_RAW_PROVIDERS:
        raise _invalid("explicit providers exceed the raw entry limit")
    for provider in providers:
        if not isinstance(provider, ProviderName):
            raise _invalid("explicit providers contain an unknown typed value")
        if provider is ProviderName.CACHE:
            raise _invalid("cache is synthetic and cannot be explicitly selected")
    normalized = tuple(stable_tier_sort(providers, deduplicate=True))
    if len(normalized) > len(PROVIDER_TIERS):
        raise _invalid("explicit providers exceed the provider catalog")
    return normalized


def _normalize_domain(raw_domain: object) -> str:
    if not isinstance(raw_domain, str) or not raw_domain:
        raise _invalid("domain constraints must be non-empty strings")
    if len(raw_domain.encode("utf-8")) > _MAX_RAW_DOMAIN_BYTES:
        raise _invalid("domain constraint exceeds the raw UTF-8 byte limit")
    if (
        raw_domain.endswith(".")
        or "://" in raw_domain
        or any(character in raw_domain for character in ("/", "?", "#", "*", "@"))
    ):
        raise _invalid("domain constraint must be a bare DNS host")
    try:
        ipaddress.ip_address(raw_domain.strip("[]"))
    except ValueError:
        pass
    else:
        raise _invalid("IP literals are not domain constraints")
    if ":" in raw_domain or "[" in raw_domain or "]" in raw_domain:
        raise _invalid("domain constraint must not contain a port or IPv6 literal")
    try:
        normalized = raw_domain.lower().encode("idna").decode("ascii")
    except UnicodeError as error:
        raise _invalid("domain constraint is not valid IDNA") from error
    if (
        not normalized
        or len(normalized.encode("ascii")) > _MAX_DOMAIN_BYTES
        or any(not _DNS_LABEL.fullmatch(label) for label in normalized.split("."))
    ):
        raise _invalid("domain constraint is not a valid bounded DNS name")
    return normalized


def _normalize_domains(domains: object) -> DomainConstraints:
    if not isinstance(domains, DomainConstraints):
        raise _invalid("domains must be typed domain constraints")
    if not isinstance(domains.include, (list, tuple)) or not isinstance(
        domains.exclude, (list, tuple)
    ):
        raise _invalid("domain constraints must be bounded sequences")
    if len(domains.include) + len(domains.exclude) > _MAX_RAW_DOMAINS:
        raise _invalid("domain constraints exceed the raw entry limit")
    include = tuple(sorted({_normalize_domain(value) for value in domains.include}))
    exclude = tuple(sorted({_normalize_domain(value) for value in domains.exclude}))
    if set(include).intersection(exclude):
        raise _invalid("include and exclude domains overlap")
    return DomainConstraints(include=include, exclude=exclude)


def _sample_utc_clock(
    utc_clock: Callable[[], datetime] | datetime,
) -> datetime:
    sampled = utc_clock() if callable(utc_clock) else utc_clock
    if not isinstance(sampled, datetime) or sampled.tzinfo is None:
        raise _invalid("utc clock must return an aware datetime")
    return sampled.astimezone(timezone.utc)


def _resolve_freshness(
    freshness: object,
    today: date,
) -> FreshnessWindow:
    if not isinstance(freshness, FreshnessWindow):
        raise _invalid("freshness must be a typed window")
    relative = freshness.requested_relative
    if relative is not None and not isinstance(relative, FreshnessRelative):
        raise _invalid("freshness contains an unknown relative-window value")
    for value in (freshness.start_date, freshness.end_date):
        if value is not None and type(value) is not date:
            raise _invalid("freshness dates must be date values")
    if relative is not None and (
        freshness.start_date is not None or freshness.end_date is not None
    ):
        raise _invalid("relative freshness cannot be mixed with explicit dates")
    if (
        freshness.start_date is not None
        and freshness.end_date is not None
        and freshness.start_date > freshness.end_date
    ):
        raise _invalid("freshness start date must not follow its end date")

    start = freshness.start_date
    end = freshness.end_date
    if relative is not None:
        days = {
            FreshnessRelative.DAY: 1,
            FreshnessRelative.WEEK: 7,
            FreshnessRelative.MONTH: 30,
            FreshnessRelative.YEAR: 365,
        }[relative]
        start = today - timedelta(days=days - 1)
        end = today
        maximum_age = min(_DEFAULT_CACHE_AGE_SECONDS, days * 86_400)
    elif start is None and end is None:
        maximum_age = _DEFAULT_CACHE_AGE_SECONDS
    elif end is not None and end < today:
        maximum_age = _DEFAULT_CACHE_AGE_SECONDS
    else:
        maximum_age = 86_400

    requested_age = freshness.max_cache_age_seconds
    if requested_age is not None:
        _require_plain_int(
            "max cache age",
            requested_age,
            minimum=1,
            maximum=maximum_age,
        )
        maximum_age = requested_age
    return FreshnessWindow(
        requested_relative=relative,
        start_date=start,
        end_date=end,
        max_cache_age_seconds=maximum_age,
    )


def _validate_optional_text(name: str, value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > _MAX_VERSION_BYTES
        or any(ord(character) < 0x20 for character in value)
    ):
        raise _invalid(f"{name} must be bounded printable text or null")
    return value


def _validate_policy(policy: object) -> ExecutionPolicySnapshot:
    if not isinstance(policy, ExecutionPolicySnapshot):
        raise _invalid("policy snapshot must be typed")
    _require_plain_int(
        "effective provider tier",
        policy.effective_max_provider_tier,
        minimum=0,
        maximum=3,
    )
    _require_plain_int(
        "plan schema version",
        policy.plan_schema_version,
        minimum=1,
        maximum=2**31 - 1,
    )
    _require_plain_int(
        "cache identity schema version",
        policy.cache_identity_schema_version,
        minimum=1,
        maximum=2**31 - 1,
    )
    if not isinstance(policy.egress_preference, EgressPreference):
        raise _invalid("egress preference contains an unknown typed value")
    for name in (
        "query_normalization_version",
        "routing_policy_version",
        "spend_policy_version",
        "freshness_policy_version",
        "domain_policy_version",
        "ranking_policy_version",
        "result_normalization_version",
    ):
        value = getattr(policy, name)
        if (
            not isinstance(value, str)
            or not value
            or len(value.encode("ascii", errors="ignore")) != len(value)
            or len(value.encode("ascii")) > _MAX_VERSION_BYTES
            or any(not 0x20 <= ord(character) <= 0x7E for character in value)
        ):
            raise _invalid(f"{name} must be printable bounded ASCII")
    if policy.allowed_providers is not None:
        allowed = _normalize_providers(policy.allowed_providers)
        if allowed is None:
            raise AssertionError("unreachable")
    return policy


def _enum_values(values: tuple[ProviderName, ...] | None) -> list[str] | None:
    if values is None:
        return None
    return [value.value for value in values]


def _date_value(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise _invalid("retrieval plan is not canonically serializable") from error


def _digest(prefix: bytes, value: object) -> str:
    return hashlib.sha256(prefix + _canonical_bytes(value)).hexdigest()


def resolve_plan(
    effective_query: SearchQuery,
    controls: RetrievalControls,
    include_attribution: bool,
    policy_snapshot: ExecutionPolicySnapshot,
    utc_clock: Callable[[], datetime] | datetime,
) -> RetrievalPlan:
    if not isinstance(effective_query, SearchQuery):
        raise _invalid("effective query must be typed")
    if not isinstance(controls, RetrievalControls):
        raise _invalid("controls must be typed")
    if not isinstance(policy_snapshot, ExecutionPolicySnapshot):
        raise _invalid("policy snapshot must be typed")
    if not isinstance(include_attribution, bool):
        raise _invalid("include attribution must be boolean")
    if not isinstance(effective_query.mode, SearchMode):
        raise _invalid("search mode contains an unknown typed value")
    if not isinstance(effective_query.free_only, bool):
        raise _invalid("free_only must be boolean")
    _validate_raw_bounds(effective_query, controls, policy_snapshot)

    normalized_query = _normalize_query(effective_query.query)
    result_limit = _require_plain_int(
        "result limit",
        effective_query.max_results,
        minimum=1,
        maximum=50,
    )
    explicit_providers = _normalize_providers(effective_query.providers)
    policy = _validate_policy(policy_snapshot)
    if not isinstance(controls.safe_search, SafeSearch):
        raise _invalid("safe search contains an unknown typed value")
    if not isinstance(controls.revalidation, RevalidationMode):
        raise _invalid("revalidation contains an unknown typed value")
    deadline_ms = _require_plain_int(
        "deadline",
        controls.deadline_ms,
        minimum=1,
        maximum=_MAX_DEADLINE_MS,
    )
    sampled_utc = _sample_utc_clock(utc_clock)
    freshness = _resolve_freshness(controls.freshness, sampled_utc.date())
    domains = _normalize_domains(controls.domains)
    country = _validate_optional_text("country", controls.country)
    language = _validate_optional_text("language", controls.language)

    if policy.allowed_providers is None:
        allowed = frozenset(PROVIDER_TIERS)
    else:
        normalized_allowed = _normalize_providers(policy.allowed_providers)
        allowed = frozenset(normalized_allowed or ())
    routed = (
        list(explicit_providers)
        if explicit_providers is not None
        else [
            provider
            for provider in resolve_routing(effective_query.mode, None)
            if provider is not ProviderName.CACHE
        ]
    )
    effective_tier = (
        0
        if effective_query.free_only
        else policy.effective_max_provider_tier
    )
    candidates = tuple(
        provider
        for provider in routed
        if provider in allowed and PROVIDER_TIERS[provider] <= effective_tier
    )

    freshness_object = {
        "requested_relative": (
            freshness.requested_relative.value
            if freshness.requested_relative is not None
            else None
        ),
        "start_date": _date_value(freshness.start_date),
        "end_date": _date_value(freshness.end_date),
        "max_cache_age_seconds": freshness.max_cache_age_seconds,
    }
    domains_object = {
        "include": list(domains.include),
        "exclude": list(domains.exclude),
    }
    versions_object = {
        "cache_identity_schema_version": policy.cache_identity_schema_version,
        "query_normalization_version": policy.query_normalization_version,
        "routing_policy_version": policy.routing_policy_version,
        "spend_policy_version": policy.spend_policy_version,
        "freshness_policy_version": policy.freshness_policy_version,
        "domain_policy_version": policy.domain_policy_version,
        "ranking_policy_version": policy.ranking_policy_version,
        "result_normalization_version": policy.result_normalization_version,
    }
    complete_plan = {
        "plan_schema_version": policy.plan_schema_version,
        "semantic": {
            "normalized_query": normalized_query,
            "intent": effective_query.mode.value,
            "result_limit": result_limit,
            "explicit_providers": _enum_values(explicit_providers),
            "freshness": freshness_object,
            "domains": domains_object,
            "safe_search": controls.safe_search.value,
            "country": country,
            "language": language,
        },
        "execution": {
            "profile": "free" if effective_query.free_only else "budgeted",
            "effective_max_provider_tier": effective_tier,
            "candidate_providers": _enum_values(candidates),
            "deadline_ms": deadline_ms,
            "revalidation": controls.revalidation.value,
            "egress_preference": policy.egress_preference.value,
        },
        "presentation": {"include_attribution": include_attribution},
        "versions": versions_object,
    }
    cache_projection = {
        "cache_identity_schema_version": policy.cache_identity_schema_version,
        "normalized_query": normalized_query,
        "intent": effective_query.mode.value,
        "result_limit": result_limit,
        "explicit_providers": _enum_values(explicit_providers),
        "freshness_resolved_dates": {
            "start_date": _date_value(freshness.start_date),
            "end_date": _date_value(freshness.end_date),
        },
        "include_domains": list(domains.include),
        "exclude_domains": list(domains.exclude),
        "safe_search": controls.safe_search.value,
        "country": country,
        "language": language,
        "query_normalization_version": policy.query_normalization_version,
        "routing_policy_version": policy.routing_policy_version,
        "freshness_policy_version": policy.freshness_policy_version,
        "domain_policy_version": policy.domain_policy_version,
        "ranking_policy_version": policy.ranking_policy_version,
        "result_normalization_version": policy.result_normalization_version,
    }
    plan_id = _digest(b"argus-plan-v1\0", complete_plan)
    cache_fingerprint = _digest(b"argus-cache-v1\0", cache_projection)

    return RetrievalPlan(
        plan_schema_version=policy.plan_schema_version,
        normalized_query=normalized_query,
        intent=effective_query.mode,
        result_limit=result_limit,
        explicit_providers=explicit_providers,
        freshness=freshness,
        domains=domains,
        safe_search=controls.safe_search,
        country=country,
        language=language,
        profile="free" if effective_query.free_only else "budgeted",
        effective_max_provider_tier=effective_tier,
        candidate_providers=candidates,
        deadline_ms=deadline_ms,
        revalidation=controls.revalidation,
        egress_preference=policy.egress_preference,
        include_attribution=include_attribution,
        cache_identity_schema_version=policy.cache_identity_schema_version,
        query_normalization_version=policy.query_normalization_version,
        routing_policy_version=policy.routing_policy_version,
        spend_policy_version=policy.spend_policy_version,
        freshness_policy_version=policy.freshness_policy_version,
        domain_policy_version=policy.domain_policy_version,
        ranking_policy_version=policy.ranking_policy_version,
        result_normalization_version=policy.result_normalization_version,
        plan_id=plan_id,
        cache_fingerprint=cache_fingerprint,
    )
