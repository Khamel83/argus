"""Canonical provider controls and hermetic environment prefixes."""

from __future__ import annotations

from datetime import date
from enum import Enum

from argus.broker.planning import FreshnessRelative, FreshnessWindow
from argus.broker.provider_evidence import (
    ControlTranslation,
    FilterStrength,
    TranslationPrecision,
)
from argus.models import ProviderName


PROVIDER_ENV_PREFIXES = tuple(
    provider.value.upper()
    for provider in ProviderName
    if provider is not ProviderName.CACHE
)
EXTRACTION_PROVIDER_ENV_PREFIXES = ("JINA", "FIRECRAWL")
HERMETIC_PROVIDER_ENV_PREFIXES = (
    *PROVIDER_ENV_PREFIXES,
    *EXTRACTION_PROVIDER_ENV_PREFIXES,
)


class FreshnessControlCapability(str, Enum):
    NONE = "none"
    RELATIVE_ONLY = "relative_only"
    DATE_RANGE = "date_range"
    RELATIVE_AND_DATE_RANGE = "relative_and_date_range"
    QUERY_QUALIFIER = "query_qualifier"


PROVIDER_CONTROL_CAPABILITIES = {
    ProviderName.SEARXNG: FreshnessControlCapability.RELATIVE_ONLY,
    ProviderName.DUCKDUCKGO: FreshnessControlCapability.RELATIVE_ONLY,
    ProviderName.YAHOO: FreshnessControlCapability.QUERY_QUALIFIER,
    ProviderName.GITHUB: FreshnessControlCapability.QUERY_QUALIFIER,
    ProviderName.WOLFRAM: FreshnessControlCapability.NONE,
    ProviderName.BRAVE: FreshnessControlCapability.RELATIVE_AND_DATE_RANGE,
    ProviderName.TAVILY: FreshnessControlCapability.RELATIVE_AND_DATE_RANGE,
    ProviderName.EXA: FreshnessControlCapability.DATE_RANGE,
    ProviderName.LINKUP: FreshnessControlCapability.DATE_RANGE,
    ProviderName.PARALLEL: FreshnessControlCapability.DATE_RANGE,
    ProviderName.SERPER: FreshnessControlCapability.NONE,
    ProviderName.YOU: FreshnessControlCapability.RELATIVE_AND_DATE_RANGE,
    ProviderName.VALYU: FreshnessControlCapability.DATE_RANGE,
    ProviderName.SEARCHAPI: FreshnessControlCapability.RELATIVE_AND_DATE_RANGE,
}


class RequiredControlUnsupported(ValueError):
    """A required plan control cannot be represented by this provider."""


_RELATIVE_VALUES = {
    FreshnessRelative.DAY: "day",
    FreshnessRelative.WEEK: "week",
    FreshnessRelative.MONTH: "month",
    FreshnessRelative.YEAR: "year",
}

_PROVIDER_RELATIVE_VALUES = {
    ProviderName.SEARXNG: {
        FreshnessRelative.DAY: "day",
        FreshnessRelative.WEEK: "month",
        FreshnessRelative.MONTH: "month",
        FreshnessRelative.YEAR: "year",
    },
    ProviderName.DUCKDUCKGO: {
        FreshnessRelative.DAY: "d",
        FreshnessRelative.WEEK: "w",
        FreshnessRelative.MONTH: "m",
        FreshnessRelative.YEAR: "y",
    },
    ProviderName.BRAVE: {
        FreshnessRelative.DAY: "pd",
        FreshnessRelative.WEEK: "pw",
        FreshnessRelative.MONTH: "pm",
        FreshnessRelative.YEAR: "py",
    },
    ProviderName.SEARCHAPI: {
        FreshnessRelative.DAY: "last_day",
        FreshnessRelative.WEEK: "last_week",
        FreshnessRelative.MONTH: "last_month",
        FreshnessRelative.YEAR: "last_year",
    },
}


def _date_range_value(start: date | None, end: date | None) -> str:
    return f"{start.isoformat() if start else ''}..{end.isoformat() if end else ''}"


def translate_freshness(
    provider: ProviderName,
    freshness: FreshnessWindow,
    *,
    required: bool,
) -> ControlTranslation:
    """Translate a validated plan freshness window without silently dropping it."""
    capability = PROVIDER_CONTROL_CAPABILITIES[provider]
    has_relative = freshness.requested_relative is not None
    has_dates = freshness.start_date is not None or freshness.end_date is not None
    if (
        provider is ProviderName.PARALLEL
        and freshness.start_date is None
        and freshness.end_date is not None
        and required
    ):
        raise RequiredControlUnsupported(
            "parallel cannot enforce an end-only freshness window"
        )
    if not has_relative and not has_dates:
        return ControlTranslation(
            capability.value,
            TranslationPrecision.EXACT,
            FilterStrength.STRICT_CONTRACT,
        )

    provider_control: str | None = None
    provider_value: str | None = None
    precision = TranslationPrecision.EXACT
    strength = FilterStrength.STRICT_CONTRACT

    if has_dates and capability in {
        FreshnessControlCapability.DATE_RANGE,
        FreshnessControlCapability.RELATIVE_AND_DATE_RANGE,
    }:
        provider_value = _date_range_value(freshness.start_date, freshness.end_date)
        provider_control = {
            ProviderName.BRAVE: "freshness",
            ProviderName.TAVILY: "start_date/end_date",
            ProviderName.EXA: "startPublishedDate/endPublishedDate",
            ProviderName.LINKUP: "fromDate/toDate",
            ProviderName.PARALLEL: "advanced_settings.source_policy.after_date",
            ProviderName.YOU: "freshness",
            ProviderName.VALYU: "start_date/end_date",
            ProviderName.SEARCHAPI: "time_period_min/time_period_max",
        }[provider]
        if provider in {ProviderName.BRAVE, ProviderName.YOU}:
            start = freshness.start_date.isoformat() if freshness.start_date else ""
            end = freshness.end_date.isoformat() if freshness.end_date else ""
            provider_value = f"{start}to{end}"
        if provider is ProviderName.PARALLEL and freshness.end_date is not None:
            precision = TranslationPrecision.WIDENED
    elif has_relative and capability in {
        FreshnessControlCapability.RELATIVE_ONLY,
        FreshnessControlCapability.RELATIVE_AND_DATE_RANGE,
    }:
        assert freshness.requested_relative is not None
        provider_value = _PROVIDER_RELATIVE_VALUES.get(provider, _RELATIVE_VALUES)[
            freshness.requested_relative
        ]
        provider_control = {
            ProviderName.SEARXNG: "time_range",
            ProviderName.DUCKDUCKGO: "timelimit",
            ProviderName.BRAVE: "freshness",
            ProviderName.TAVILY: "time_range",
            ProviderName.YOU: "freshness",
            ProviderName.SEARCHAPI: "time_period",
        }[provider]
        if provider is ProviderName.DUCKDUCKGO:
            strength = FilterStrength.BEST_EFFORT
        if (
            provider is ProviderName.SEARXNG
            and freshness.requested_relative is FreshnessRelative.WEEK
        ):
            precision = TranslationPrecision.WIDENED
    elif capability is FreshnessControlCapability.QUERY_QUALIFIER:
        if has_dates:
            provider_control = "query_qualifier"
            provider_value = _date_range_value(freshness.start_date, freshness.end_date)
            strength = FilterStrength.BEST_EFFORT
        else:
            precision = TranslationPrecision.UNSUPPORTED
            strength = FilterStrength.UNKNOWN
    else:
        precision = TranslationPrecision.UNSUPPORTED
        strength = FilterStrength.UNKNOWN

    translation = ControlTranslation(
        capability.value,
        precision,
        strength,
        provider_control,
        provider_value,
        requested_relative=(
            freshness.requested_relative.value
            if freshness.requested_relative is not None
            else None
        ),
        resolved_start_date=freshness.start_date,
        resolved_end_date=freshness.end_date,
        applied_start_date=(
            freshness.start_date
            if precision is TranslationPrecision.EXACT
            and strength is FilterStrength.STRICT_CONTRACT
            else None
        ),
        applied_end_date=(
            freshness.end_date
            if precision is TranslationPrecision.EXACT
            and strength is FilterStrength.STRICT_CONTRACT
            else None
        ),
    )
    if required and precision is TranslationPrecision.UNSUPPORTED:
        raise RequiredControlUnsupported(
            f"{provider.value} cannot apply the required freshness control"
        )
    return translation
