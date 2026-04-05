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
    result = await extract_url(req.url, domain=req.domain)
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


@router.get("/cookies/health")
async def cookie_health():
    """Get health status of all configured cookie domains."""
    from argus.extraction.cookies import get_health_summary
    return get_health_summary()


@router.post("/auth/refresh/{domain}")
async def auth_refresh_domain(domain: str):
    """Trigger a cookie login refresh for a specific domain via the extract service."""
    from argus.extraction.health_poller import refresh_domain, MANUAL_ONLY_DOMAINS
    import os

    if domain in MANUAL_ONLY_DOMAINS:
        return {"success": False, "message": f"{domain} is manual-only (no auto-login)"}

    remote_url = os.getenv("ARGUS_REMOTE_EXTRACT_URL", "")
    remote_key = os.getenv("ARGUS_REMOTE_EXTRACT_KEY", "")
    if not remote_url:
        return {"success": False, "message": "ARGUS_REMOTE_EXTRACT_URL not configured"}

    result = await refresh_domain(domain, remote_url, remote_key)
    return result


@router.post("/auth/refresh")
async def auth_refresh_all():
    """Trigger cookie login refresh for all stale domains."""
    from argus.extraction.health_poller import refresh_stale_domains

    results = await refresh_stale_domains()
    if not results:
        return {"refreshed": 0, "message": "no stale domains or service not configured"}

    succeeded = [d for d, r in results.items() if r.get("success")]
    failed = {d: r.get("message", "unknown") for d, r in results.items() if not r.get("success")}
    return {
        "refreshed": len(succeeded),
        "succeeded": succeeded,
        "failed": failed,
    }
