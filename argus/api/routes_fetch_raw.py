"""Thin HTTP presentation for bounded raw browser fetches."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from argus.api.schemas import FetchRawRequest, FetchRawResponse
from argus.raw_fetch import fetch_raw

router = APIRouter()


@router.post("/fetch-raw", response_model=FetchRawResponse)
async def fetch_raw_route(request: FetchRawRequest):
    result = await fetch_raw(request)
    if result.status == "error":
        return JSONResponse(
            status_code=result.http_status or 502,
            content=result.model_dump(exclude_none=True),
        )
    return result
