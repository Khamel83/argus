"""Legacy search routes projected from the accepted-operation authority."""

from fastapi import APIRouter, Depends, Request

from argus.api.presenters import LegacyHttpPresenter
from argus.api.schemas import ExpandRequest, RecoverUrlRequest, SearchRequest, SearchResponse
from argus.operations.accepted import AcceptedOperationService

router = APIRouter()
_presenter = LegacyHttpPresenter()


def get_accepted_operation_service(request: Request) -> AcceptedOperationService:
    return request.app.state.get_accepted_operation_service()


def _principal(request: Request) -> str:
    return getattr(request.state, "caller_identity", "") or "unknown"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


@router.post("/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    request: Request,
    service: AcceptedOperationService = Depends(get_accepted_operation_service),
):
    operation = await service.search(
        req,
        principal=_principal(request),
        request_id=_request_id(request),
    )
    return _presenter.search(operation)


@router.post("/recover-url", response_model=SearchResponse)
async def recover_url(
    req: RecoverUrlRequest,
    request: Request,
    service: AcceptedOperationService = Depends(get_accepted_operation_service),
):
    operation = await service.recover(
        req,
        principal=_principal(request),
        request_id=_request_id(request),
    )
    return _presenter.search(operation)


@router.post("/expand", response_model=SearchResponse)
async def expand(
    req: ExpandRequest,
    request: Request,
    service: AcceptedOperationService = Depends(get_accepted_operation_service),
):
    operation = await service.expand(
        req,
        principal=_principal(request),
        request_id=_request_id(request),
    )
    return _presenter.search(operation)
