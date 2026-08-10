"""Deterministic targeted-research planning and workflow semantics."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _target(name: str, prefix: str, *requirements: tuple[str, str]):
    return {
        "name": name,
        "source_prefixes": [prefix],
        "requirements": [
            {"claim_class": claim_class, "query": query}
            for claim_class, query in requirements
        ],
    }


def _accepted(*urls: str, receipt: str = "receipt-1"):
    return SimpleNamespace(
        outcome=SimpleNamespace(value="success"),
        request_id="search-request",
        result={
            "results": tuple({"url": url, "title": url.rsplit("/", 1)[-1]} for url in urls),
            "acceptance_receipt": {"receipt_ref": receipt},
        },
        error=None,
    )


def test_target_planner_keeps_input_order_and_exact_accepted_urls():
    from argus.workflows.targeted_research import plan_target_research

    targets = [
        _target(
            "First",
            "https://docs.example.com/sdk/",
            ("capabilities", "first capabilities"),
            ("pricing_eligibility", "first pricing"),
        ),
        _target("Second", "https://other.example.org/guide", ("capabilities", "second")),
    ]
    searches = [
        _accepted(
            "HTTPS://DOCS.EXAMPLE.COM/sdk/intro/",
            "https://docs.example.com/sdk/intro/",
            "https://docs.example.com/sdk/intro/other",
            "https://outside.example.net/secondary",
            receipt="receipt-first-capabilities",
        ),
        _accepted(
            "https://docs.example.com/sdk/pricing/",
            "https://docs.example.com/sdk/pricing/other",
            receipt="receipt-first-pricing",
        ),
        _accepted(
            "https://other.example.org/guide/start",
            "https://other.example.org/guide/start/extra",
            receipt="receipt-second-capabilities",
        ),
    ]

    plan = plan_target_research(
        targets,
        searches,
        max_research_pages=5,
    )

    assert [item.requirement_ref for item in plan.requirements] == [
        "target-0-requirement-0",
        "target-0-requirement-1",
        "target-1-requirement-0",
    ]
    assert plan.requirements[0].candidates[0].url == "HTTPS://DOCS.EXAMPLE.COM/sdk/intro/"
    assert [candidate.url for candidate in plan.requirements[0].candidates] == [
        "HTTPS://DOCS.EXAMPLE.COM/sdk/intro/",
        "https://docs.example.com/sdk/intro/other",
    ]
    assert plan.requirements[0].request_id != plan.requirements[1].request_id
    assert plan.requirements[0].request_id == plan.requirements[0].request_id
    assert plan.external_candidates[0].url == "https://outside.example.net/secondary"


def test_target_planner_uses_path_boundaries_and_global_canonical_dedupe():
    from argus.workflows.targeted_research import plan_target_research

    targets = [
        _target("One", "https://docs.example.com/sdk/", ("capabilities", "one")),
        _target("Two", "https://other.example.com/", ("capabilities", "two")),
    ]
    searches = [
        _accepted(
            "https://docs.example.com/sdkmatic/no",
            "https://docs.example.com/sdk/guide",
            "https://docs.example.com/sdk/guide/",
            "https://other.example.com/elsewhere",
            receipt="receipt-one",
        ),
        _accepted(
            "https://other.example.com/",
            "https://outside.example.net/a",
            receipt="receipt-two",
        ),
    ]

    plan = plan_target_research(targets, searches, max_research_pages=4)

    assert [candidate.url for candidate in plan.requirements[0].candidates] == [
        "https://docs.example.com/sdk/guide"
    ]
    assert [candidate.url for candidate in plan.requirements[1].candidates] == [
        "https://other.example.com/"
    ]
    assert [candidate.url for candidate in plan.external_candidates] == [
        "https://outside.example.net/a"
    ]


@pytest.mark.parametrize(
    ("requirement_count", "page_budget", "expected_external_slots"),
    [
        (0, 1, 0),
        (1, 2, 1),
        (15, 16, 1),
        (15, 17, 2),
        (16, 17, 1),
        (16, 18, 2),
    ],
)
def test_target_page_math_is_derived_from_request(
    requirement_count, page_budget, expected_external_slots
):
    from argus.workflows.targeted_research import page_budget_math

    math = page_budget_math(requirement_count, page_budget)
    assert math.required_target_pages == requirement_count
    assert math.mandatory_external_pages == (1 if requirement_count else 0)
    assert math.optional_external_pages == max(expected_external_slots - 1, 0)
    assert math.external_extraction_candidates <= 4


def test_target_planner_rejects_budget_overrun_without_frozen_15_requirement_constant():
    from argus.workflows.targeted_research import TargetWorkflowFailure, plan_target_research

    targets = [_target("One", "https://docs.example.com/", ("capabilities", "one"))]
    with pytest.raises(TargetWorkflowFailure) as caught:
        plan_target_research(targets, [_accepted("https://docs.example.com/one")], max_research_pages=1)
    assert caught.value.code == "workflow_page_budget_exceeded"


class _TargetOperations:
    def __init__(self, results, *, failed_urls=(), timeout=False, unready=False):
        self.results = tuple(results)
        self.failed_urls = set(failed_urls)
        self.timeout = timeout
        self.unready = unready
        self.search_calls = []
        self.compose_calls = []

    async def search(self, request, *, principal, request_id):
        del principal
        self.search_calls.append((request, request_id))
        if self.timeout:
            raise TimeoutError("provider timeout")
        from argus.contracts import AcceptedOperation, CanonicalOutcome
        from argus.operations.accepted import _operation_error

        if self.unready:
            outcome = CanonicalOutcome.UNREADY
            return AcceptedOperation(
                outcome=outcome,
                request_id=request_id,
                result=None,
                error=_operation_error(outcome, request_id=request_id, detail="not ready"),
            )
        index = len(self.search_calls) - 1
        return AcceptedOperation(
            outcome=CanonicalOutcome.SUCCESS,
            request_id=request_id,
            result={
                "results": tuple(self.results[index]),
                "acceptance_receipt": {"receipt_ref": f"receipt-target-{index}"},
            },
            error=None,
        )

    async def compose_workflow(
        self,
        retrieval,
        *,
        max_results,
        principal,
        request_id,
        selection_urls=None,
        **kwargs,
    ):
        del principal, kwargs
        from argus.contracts import AcceptedOperation, CanonicalOutcome
        from argus.operations.accepted import _operation_error

        url = selection_urls[0]
        self.compose_calls.append(
            {"url": url, "request_id": request_id, "max_results": max_results}
        )
        if url in self.failed_urls:
            outcome = CanonicalOutcome.EXTRACTION_FAILED
            return AcceptedOperation(
                outcome=outcome,
                request_id=request_id,
                result={"artifacts": (), "links": ()},
                error=_operation_error(
                    outcome,
                    request_id=request_id,
                    detail="candidate failed",
                ),
            )
        return AcceptedOperation(
            outcome=CanonicalOutcome.SUCCESS,
            request_id=request_id,
            result={
                "composition_receipt_ref": f"composition-{request_id}",
                "accepted_artifact_refs": (f"artifact-{request_id}",),
                "degraded_artifact_refs": (),
                "rejected_extraction_refs": (),
                "composition_trace": ("artifact_floor_met",),
                "links": ({"artifact_disposition": "usable"},),
                "artifacts": (
                    {
                        "url": url,
                        "title": "Accepted",
                        "text": "accepted target content",
                        "word_count": 3,
                        "disposition": "usable",
                        "extractor": "test",
                    },
                ),
            },
            error=None,
        )


def _service(monkeypatch, tmp_path, operations):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    from argus.workflows.service import WorkflowService

    return WorkflowService(operations)


@pytest.mark.asyncio
async def test_targeted_service_searches_once_per_requirement_and_retries_private_candidates(
    monkeypatch, tmp_path
):
    from argus.workflows.targeted_research import derive_requirement_request_id
    from argus.workflows.models import WorkflowStatus

    target = _target(
        "Docs",
        "https://docs.example.com/sdk/",
        ("capabilities", "sdk capabilities"),
    )
    operations = _TargetOperations(
        [
            [
                {"url": "https://docs.example.com/sdk/first", "title": "first"},
                {"url": "https://docs.example.com/sdk/second", "title": "second"},
                {"url": "https://external.example.net/source", "title": "external"},
            ]
        ],
        failed_urls={"https://docs.example.com/sdk/first"},
    )
    service = _service(monkeypatch, tmp_path, operations)

    result = await service.build_research_pack(
        topic="SDK",
        max_research_pages=2,
        research_targets=[target],
    )

    assert result.status is WorkflowStatus.COMPLETED
    assert len(operations.search_calls) == 1
    assert operations.search_calls[0][0].mode == "research"
    assert operations.search_calls[0][0].max_results == 8
    assert [document.url for document in result.documents] == [
        "https://docs.example.com/sdk/second",
        "https://external.example.net/source",
    ]
    assert operations.compose_calls[0]["request_id"] == derive_requirement_request_id(
        "receipt-target-0", "target-0-requirement-0"
    )
    assert operations.compose_calls[0]["request_id"] == operations.compose_calls[1][
        "request_id"
    ]
    assert "compositions" not in result.metadata


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"timeout": True}, "workflow_required_target_search_timeout"),
        ({"unready": True}, "unready"),
    ],
)
async def test_targeted_service_preserves_search_failure_codes(
    monkeypatch, tmp_path, kwargs, expected
):
    from argus.workflows.models import WorkflowStatus

    operations = _TargetOperations(
        [[{"url": "https://docs.example.com/sdk/one", "title": "one"}]],
        **kwargs,
    )
    result = await _service(monkeypatch, tmp_path, operations).build_research_pack(
        topic="SDK",
        max_research_pages=2,
        research_targets=[
            _target("Docs", "https://docs.example.com/sdk/", ("capabilities", "sdk"))
        ],
    )
    assert result.status is WorkflowStatus.FAILED
    assert result.error == expected
    assert not result.error.startswith("workflow_composition_")


@pytest.mark.asyncio
async def test_targeted_service_reports_all_failed_candidates_without_leaking_diagnostics(
    monkeypatch, tmp_path
):
    from argus.workflows.models import WorkflowStatus

    first = "https://docs.example.com/sdk/first"
    second = "https://docs.example.com/sdk/second"
    operations = _TargetOperations(
        [[
            {"url": first, "title": "first"},
            {"url": second, "title": "second"},
            {"url": "https://external.example.net/source", "title": "external"},
        ]],
        failed_urls={first, second},
    )
    result = await _service(monkeypatch, tmp_path, operations).build_research_pack(
        topic="SDK",
        max_research_pages=2,
        research_targets=[
            _target("Docs", "https://docs.example.com/sdk/", ("capabilities", "sdk"))
        ],
    )
    assert result.status is WorkflowStatus.FAILED
    assert result.error == "workflow_required_target_extraction_failed"
    assert first not in result.metadata.get("failure", {})
    assert second not in result.metadata.get("failure", {})


@pytest.mark.asyncio
async def test_targeted_service_marks_optional_external_as_degraded_and_caps_attempts(
    monkeypatch, tmp_path
):
    from argus.workflows.models import WorkflowStatus

    external_domains = ("example.net", "example.org", "example.io", "example.co.uk")
    external = [
        {"url": f"https://external{i}.{domain}/source", "title": str(i)}
        for i, domain in enumerate(external_domains)
    ]
    operations = _TargetOperations(
        [[
            {"url": "https://docs.example.com/sdk/one", "title": "one"},
            *external,
        ]],
        failed_urls={item["url"] for item in external[1:]},
    )
    result = await _service(monkeypatch, tmp_path, operations).build_research_pack(
        topic="SDK",
        max_research_pages=5,
        research_targets=[
            _target("Docs", "https://docs.example.com/sdk/", ("capabilities", "sdk"))
        ],
    )
    assert result.status is WorkflowStatus.COMPLETED
    assert result.metadata["degraded_reasons"] == ["degraded_external_unavailable"]
    external_calls = [
        call for call in operations.compose_calls if call["url"].startswith("https://external")
    ]
    assert len(external_calls) == 4
    assert len(result.documents) == 2


@pytest.mark.asyncio
async def test_targeted_service_fails_before_search_when_budget_is_too_small(
    monkeypatch, tmp_path
):
    from argus.workflows.models import WorkflowStatus

    operations = _TargetOperations([[]])
    result = await _service(monkeypatch, tmp_path, operations).build_research_pack(
        topic="SDK",
        max_research_pages=1,
        research_targets=[
            _target("Docs", "https://docs.example.com/sdk/", ("capabilities", "sdk"))
        ],
    )
    assert result.status is WorkflowStatus.FAILED
    assert result.error == "workflow_page_budget_exceeded"
    assert operations.search_calls == []
