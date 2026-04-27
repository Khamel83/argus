"""
Content extraction endpoint.
"""

from fastapi import APIRouter

from argus.api.schemas import (
    AssessContentRequest,
    AssessContentResponse,
    ExtractRequest,
    ExtractResponse,
)
from argus.extraction import extract_url
from argus.extraction.completeness import assess_completeness

router = APIRouter()


@router.post("/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest):
    """Extract clean text content from a URL."""
    result = await extract_url(req.url, domain=req.domain)
    cr = result.completeness_result
    return ExtractResponse(
        url=result.url,
        title=result.title,
        text=result.text,
        author=result.author,
        date=result.date,
        word_count=result.word_count,
        extractor=result.extractor.value if result.extractor else None,
        error=result.error,
        quality_passed=getattr(result, "quality_passed", None),
        quality_reason=getattr(result, "quality_reason", None),
        extractors_tried=getattr(result, "extractors_tried", None),
        is_complete=cr.is_complete if cr else None,
        completeness_confidence=cr.confidence if cr else None,
        truncation_type=cr.truncation_type if cr else None,
        completeness_signals=cr.signals if cr else None,
        recommended_action=cr.recommended_action if cr else None,
    )


@router.post("/assess-content", response_model=AssessContentResponse)
async def assess_content(req: AssessContentRequest):
    """Assess whether provided text looks like a complete article.

    Lightweight endpoint — no fetching, pure heuristics. Useful for callers
    that already have text (e.g. feed items, stored articles) and want to know
    whether to try fetching the full version.
    """
    result = assess_completeness(req.text, req.url)
    return AssessContentResponse(
        is_complete=result.is_complete,
        confidence=result.confidence,
        truncation_type=result.truncation_type,
        signals=result.signals,
        word_count=result.word_count,
        recommended_action=result.recommended_action,
    )


@router.get("/cookies/health")
async def cookie_health():
    """Get health status of all configured cookie domains."""
    from argus.extraction.cookies import get_health_summary
    import json
    return get_health_summary()
