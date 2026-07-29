"""HTTP application operations for local extraction diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from argus.extraction.completeness import assess_completeness
from argus.extraction.cookies import get_health_summary


@dataclass(frozen=True)
class ContentAssessmentFacts:
    is_complete: bool
    confidence: float
    truncation_type: str
    signals: tuple[str, ...]
    word_count: int
    recommended_action: str


def assess_content_facts(text: str, url: str) -> ContentAssessmentFacts:
    result = assess_completeness(text, url)
    return ContentAssessmentFacts(
        is_complete=result.is_complete,
        confidence=result.confidence,
        truncation_type=result.truncation_type,
        signals=tuple(result.signals),
        word_count=result.word_count,
        recommended_action=result.recommended_action,
    )


@dataclass(frozen=True)
class CookieHealthFacts:
    values: dict[str, Any]


def cookie_health_facts() -> CookieHealthFacts:
    return CookieHealthFacts(dict(get_health_summary()))
