"""Additive evidence-rich HTTP routes."""

from fastapi import APIRouter, Depends, Request

from argus.api.contracts_v2 import (
    EvidenceHttpPresenter,
    admission_operation,
)
from argus.api.schemas import ExpandRequest, ExtractRequest, RecoverUrlRequest, SearchRequest
from argus.contracts import CanonicalOutcome
from argus.operations.accepted import AcceptedOperationService

router = APIRouter()
_presenter = EvidenceHttpPresenter()


def get_accepted_operation_service(request: Request) -> AcceptedOperationService:
    return request.app.state.get_accepted_operation_service()


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _principal(request: Request) -> str:
    return getattr(request.state, "caller_identity", "") or "unknown"


def _disabled(request: Request):
    if request.app.state.evidence_authority_enabled:
        return None
    return _presenter.response(
        admission_operation(
            outcome=CanonicalOutcome.UNREADY,
            request_id=_request_id(request),
            detail="Evidence authority is not enabled",
        )
    )


@router.post("/search")
async def search_v2(
    req: SearchRequest,
    request: Request,
    service: AcceptedOperationService = Depends(get_accepted_operation_service),
):
    if response := _disabled(request):
        return response
    operation = await service.search(
        req,
        principal=_principal(request),
        request_id=_request_id(request),
        require_owned_session=True,
    )
    return _presenter.response(operation)


@router.post("/recover-url")
async def recover_v2(
    req: RecoverUrlRequest,
    request: Request,
    service: AcceptedOperationService = Depends(get_accepted_operation_service),
):
    if response := _disabled(request):
        return response
    operation = await service.recover(
        req,
        principal=_principal(request),
        request_id=_request_id(request),
    )
    return _presenter.response(operation)


@router.post("/expand")
async def expand_v2(
    req: ExpandRequest,
    request: Request,
    service: AcceptedOperationService = Depends(get_accepted_operation_service),
):
    if response := _disabled(request):
        return response
    operation = await service.expand(
        req,
        principal=_principal(request),
        request_id=_request_id(request),
    )
    return _presenter.response(operation)


@router.post("/extract")
async def extract_v2(
    req: ExtractRequest,
    request: Request,
    service: AcceptedOperationService = Depends(get_accepted_operation_service),
):
    if response := _disabled(request):
        return response
    operation = await service.extract(
        req,
        principal=_principal(request),
        request_id=_request_id(request),
    )
    return _presenter.response(operation)
