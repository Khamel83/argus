"""Workflow evidence composition and rejected-content safety."""

import pytest

from argus.contracts import AcceptedOperation, CanonicalOutcome
from argus.operations.accepted import _operation_error


def _error(outcome, request_id):
    return _operation_error(
        outcome,
        request_id=request_id,
        detail="workflow composition failed",
    )


@pytest.mark.asyncio
async def test_rejected_artifact_never_reaches_document_or_summarizer(
    monkeypatch, tmp_path
):
    """A failed composition preserves its link but cannot synthesize or deliver."""
    from argus.workflows import WorkflowService
    from argus.workflows import service as workflow_service

    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))

    class Operations:
        async def search(self, request, *, principal, request_id):
            return AcceptedOperation(
                outcome=CanonicalOutcome.SUCCESS,
                request_id="workflow-search-2",
                result={
                    "results": (
                        {"url": "https://example.com/rejected", "title": "Rejected"},
                    ),
                    "acceptance_receipt": {"receipt_ref": "receipt-search-2"},
                },
                error=None,
            )

        async def compose_workflow(
            self, retrieval, *, max_results, principal, request_id
        ):
            return AcceptedOperation(
                outcome=CanonicalOutcome.EXTRACTION_FAILED,
                request_id=request_id,
                result=None,
                error=_error(CanonicalOutcome.EXTRACTION_FAILED, request_id),
            )

    class Gateway:
        def __init__(self):
            self.compositions = []

    def prohibited_summarizer(*args, **kwargs):
        raise AssertionError("rejected content reached the summarizer")

    gateway = Gateway()
    monkeypatch.setattr(workflow_service, "get_summarizer", prohibited_summarizer)
    result = await WorkflowService(Operations(), gateway).search_and_summarize(
        query="rejected evidence", max_search_results=1
    )

    assert result.status.value == "failed"
    assert result.documents == []
    assert result.citations == []
    assert result.report_path is None
    assert gateway.compositions == []
