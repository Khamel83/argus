"""Workflow evidence composition and rejected-content safety."""

from types import SimpleNamespace

import pytest

from argus.contracts import AcceptedOperation, CanonicalOutcome


def test_workflow_evidence_view_derives_stable_bounded_cluster_refs():
    """The workflow adapts only immutable accepted search facts."""
    from argus.workflows.models import WorkflowEvidenceView

    operation = AcceptedOperation(
        outcome=CanonicalOutcome.SUCCESS,
        request_id="workflow-search-1",
        result={
            "results": (
                {"url": "https://example.com/one", "title": "One"},
                {"url": "https://example.com/two", "title": "Two"},
            ),
            "acceptance_receipt": {"receipt_ref": "receipt-search-1"},
        },
        error=None,
    )

    view = WorkflowEvidenceView.from_operation(operation, max_results=2)

    assert view.outcome is CanonicalOutcome.SUCCESS
    assert view.acceptance_receipt == "receipt-search-1"
    assert view.result_cluster_refs == (
        "workflow-search-1-0",
        "workflow-search-1-1",
    )


@pytest.mark.asyncio
async def test_rejected_artifact_never_reaches_document_or_summarizer(
    monkeypatch, tmp_path
):
    """A failed composition preserves its link but cannot synthesize or deliver."""
    from argus.extraction.outcomes import ArtifactDisposition
    from tests.test_extraction_composition import _link
    from argus.workflows import WorkflowService
    from argus.workflows import service as workflow_service

    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    rejected = _link(
        outcome=CanonicalOutcome.EXTRACTION_FAILED,
        disposition=ArtifactDisposition.DIAGNOSTIC_ONLY,
    ).accepted_outcome

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

        async def extract(self, request, *, principal, request_id):
            return AcceptedOperation(
                outcome=CanonicalOutcome.SUCCESS,
                request_id="workflow-extract-2",
                result={"extraction_run_id": rejected.extraction_run_id},
                error=None,
            )

    class Gateway:
        def __init__(self):
            self.compositions = []

        def load_accepted_extraction_outcome(self, extraction_run_id):
            assert extraction_run_id == rejected.extraction_run_id
            return rejected

        def accept_retrieval_composition(self, view, composition, requirement):
            self.compositions.append(composition)
            return SimpleNamespace(receipt_ref="composition-2")

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
    assert (
        gateway.compositions[0].composite_outcome is CanonicalOutcome.EXTRACTION_FAILED
    )
