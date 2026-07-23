"""
Content extraction endpoint.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from argus.api.schemas import (
    AssessContentRequest,
    AssessContentResponse,
    ExtractRequest,
    ExtractResponse,
)
from argus.extraction import extract_url
from argus.extraction.completeness import assess_completeness
from argus.logging import get_logger

logger = get_logger("api.extract")
router = APIRouter()


def get_persistence_repository(request: Request):
    return request.app.state.get_search_repository()


@router.post("/extract", response_model=ExtractResponse)
async def extract(
    req: ExtractRequest,
    repository=Depends(get_persistence_repository),
):
    """Extract clean text content from a URL."""
    if req.caller:
        logger.info("extract caller=%s url=%s", req.caller, req.url)
    try:
        result = await extract_url(
            req.url,
            domain=req.domain,
            mode=req.mode,
            caller=req.caller,
            repository=repository,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Extraction could not be durably recorded",
        ) from exc
    cr = result.completeness_result
    return ExtractResponse(
        extraction_run_id=result.extraction_run_id,
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
        # Provenance
        source_type=getattr(result, "source_type", None),
        egress=getattr(result, "egress", None),
        machine=getattr(result, "machine", None),
        auth_used=getattr(result, "auth_used", False),
        cookies_used=getattr(result, "cookies_used", False),
        archive_used=getattr(result, "archive_used", False),
        cost=getattr(result, "cost", 0.0),
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


@router.get("/admin/cookies/health")
async def cookie_health():
    """Get health status of all configured cookie domains."""
    from argus.extraction.cookies import get_health_summary
    return get_health_summary()
