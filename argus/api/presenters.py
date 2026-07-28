"""Pure HTTP projections of immutable accepted operations."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import HTTPException

from argus.api.schemas import ExtractResponse, SearchResponse
from argus.contracts import AcceptedOperation, is_success_like


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


class LegacyHttpPresenter:
    """Preserve v1 response shapes without owning semantic decisions."""

    @staticmethod
    def _result(operation: AcceptedOperation) -> dict:
        if not is_success_like(operation.outcome):
            error = operation.error
            raise HTTPException(
                status_code=error.status if error is not None else 503,
                detail=(
                    error.detail
                    if error is not None
                    else "Accepted operation unavailable"
                ),
            )
        return _thaw(operation.result)

    def search(self, operation: AcceptedOperation) -> SearchResponse:
        result = self._result(operation)
        result.pop("acceptance_receipt", None)
        return SearchResponse.model_validate(result)

    def extract(self, operation: AcceptedOperation) -> ExtractResponse:
        # V1 historically returned a typed rejection in a 200 response when
        # all extractors failed. Preserve that wire contract while V2 exposes
        # the canonical failure status.
        if (
            operation.outcome.value == "extraction_failed"
            and operation.result is not None
        ):
            return ExtractResponse.model_validate(_thaw(operation.result))
        return ExtractResponse.model_validate(self._result(operation))
