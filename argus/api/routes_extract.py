"""
Content extraction endpoint.
"""

from fastapi import APIRouter, Depends, Request

from argus.api.presenters import LegacyHttpPresenter
from argus.api.schemas import (
    AssessContentRequest,
    AssessContentResponse,
    ExtractRequest,
    ExtractResponse,
)
from argus.extraction.completeness import assess_completeness
from argus.logging import get_logger
from argus.operations.accepted import AcceptedOperationService

logger = get_logger("api.extract")
router = APIRouter()
_presenter = LegacyHttpPresenter()


def get_accepted_operation_service(request: Request) -> AcceptedOperationService:
    return request.app.state.get_accepted_operation_service()


@router.post("/extract", response_model=ExtractResponse)
async def extract(
    req: ExtractRequest,
    request: Request,
    service: AcceptedOperationService = Depends(get_accepted_operation_service),
):
    """Extract clean text content from a URL."""
    authenticated_caller = getattr(request.state, "caller_identity", "") or "unknown"
    logger.info(
        "extract request_id=%s caller=%s",
        getattr(request.state, "request_id", "unknown"),
        authenticated_caller,
    )
    operation = await service.extract(
        req,
        principal=authenticated_caller,
        request_id=getattr(request.state, "request_id", "unknown"),
    )
    return _presenter.extract(operation)


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
