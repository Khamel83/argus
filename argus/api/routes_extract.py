"""
Content extraction endpoint.
"""

from fastapi import APIRouter

from argus.api.schemas import ExtractRequest, ExtractResponse
from argus.extraction import extract_url

router = APIRouter()


@router.post("/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest):
    """Extract clean text content from a URL."""
    result = await extract_url(req.url)
    return ExtractResponse(
        url=result.url,
        title=result.title,
        text=result.text,
        author=result.author,
        date=result.date,
        word_count=result.word_count,
        extractor=result.extractor.value if result.extractor else None,
        error=result.error,
    )
