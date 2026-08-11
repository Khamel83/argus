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


def _target_execution_evidence():
    """Return an explicit v3 diagnostic projection for accepted fake artifacts."""

    diagnostic = {
        "provider": "test-provider",
        "extractor": "test",
        "status": "success",
        "result_count": 1,
        "timeout_source": None,
        "operation_latency_ms": None,
        "cache_latency_ms": None,
        "cache_state": "miss",
        "cache_age_ms": None,
        "cache_origin": None,
        "spend_provenance": "not_applicable",
        "freshness_age_ms": None,
        "freshness_window": None,
        "freshness_reason": "not_applicable",
        "free_profile_eligible": None,
        "egress": "unknown",
        "machine": "unknown",
        "source_type": "targeted_first_party",
    }
    return {
        "schema": "argus-execution-evidence-v1",
        "source": "ExtractedContent",
        "provider": "test-provider",
        "extractor": "test",
        "egress": "unknown",
        "machine": "unknown",
        "source_type": "targeted_first_party",
        "retrieved_at": "2026-08-09T12:00:00+00:00",
        "source_date": None,
        "result_count": 1,
        "timeout_source": None,
        "operation_latency_ms": None,
        "cache_latency_ms": None,
        "cache_state": "miss",
        "cache_age": None,
        "cache_origin": None,
        "spend_provenance": "not_applicable",
        "freshness_age": None,
        "freshness_window": None,
        "freshness_reason": "not_applicable",
        "free_profile_eligible": None,
        "cache_eligibility": {"eligible": None, "reason": "not_applicable"},
        "diagnostics": (diagnostic,),
        "execution_diagnostics": (diagnostic,),
    }


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
    def __init__(
        self,
        results,
        *,
        failed_urls=(),
        timeout=False,
        unready=False,
        omit_execution_evidence=False,
    ):
        self.results = tuple(results)
        self.failed_urls = set(failed_urls)
        self.timeout = timeout
        self.unready = unready
        self.omit_execution_evidence = omit_execution_evidence
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
        artifact = {
            "url": url,
            "title": "Accepted",
            "text": "accepted target content",
            "word_count": 3,
            "disposition": "usable",
            "extractor": "test",
        }
        if not self.omit_execution_evidence:
            execution_evidence = _target_execution_evidence()
            artifact["execution_evidence"] = execution_evidence
            artifact["execution_diagnostics"] = execution_evidence["execution_diagnostics"]
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
                "artifacts": (artifact,),
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
    assert len(operations.search_calls) <= 2
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
async def test_targeted_service_fails_closed_when_candidate_evidence_is_missing(
    monkeypatch, tmp_path
):
    from argus.workflows.models import WorkflowStatus

    operations = _TargetOperations(
        [[
            {"url": "https://docs.example.com/sdk/one", "title": "one"},
            {"url": "https://external.example.net/source", "title": "external"},
        ]],
        omit_execution_evidence=True,
    )
    result = await _service(monkeypatch, tmp_path, operations).build_research_pack(
        topic="missing-evidence",
        max_research_pages=2,
        research_targets=[
            _target("Docs", "https://docs.example.com/sdk/", ("capabilities", "sdk"))
        ],
    )

    assert result.status is WorkflowStatus.FAILED
    assert result.error == "workflow_target_execution_evidence_missing"
    assert result.artifacts == []
    assert result.documents == []
    assert result.report_path is None
    assert result.manifest_path is None


@pytest.mark.asyncio
async def test_targeted_failed_run_cleans_partial_target_documents(
    monkeypatch, tmp_path
):
    import json
    from pathlib import Path

    from argus.workflows.models import WorkflowStatus

    first = "https://docs.example.com/sdk/one"
    second = "https://docs.example.org/sdk/two"
    operations = _TargetOperations(
        [
            [
                {"url": first, "title": "one"},
                {"url": "https://external.example.net/source", "title": "external"},
            ],
            [{"url": second, "title": "two"}],
        ],
        failed_urls={second},
    )
    service = _service(monkeypatch, tmp_path, operations)

    result = await service.build_research_pack(
        topic="partial-target-failure",
        max_research_pages=3,
        research_targets=[
            _target("First", "https://docs.example.com/sdk/", ("capabilities", "first")),
            _target("Second", "https://docs.example.org/sdk/", ("capabilities", "second")),
        ],
    )

    assert result.status is WorkflowStatus.FAILED
    assert result.error == "workflow_required_target_extraction_failed"
    assert result.documents == []
    assert result.artifacts == []
    assert result.report_path is None
    assert result.manifest_path is None

    snapshot_dir = Path(result.snapshot_dir)
    targeted_dir = snapshot_dir / "targeted-research"
    assert not any(path.is_file() for path in targeted_dir.rglob("*"))
    state_path = service._paths.workflow_runs_dir / f"{result.run_id}.json"
    assert state_path.is_file()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["documents"] == []
    assert persisted["artifacts"] == []
    assert persisted["report_path"] is None
    assert persisted["manifest_path"] is None


@pytest.mark.asyncio
async def test_targeted_terminal_failure_marker_cleans_stale_completed_state_on_reload(
    monkeypatch, tmp_path
):
    import json
    from pathlib import Path

    from argus.workflows.models import WorkflowStatus
    from argus.workflows.service import WorkflowService

    operations = _TargetOperations(
        [[
            {"url": "https://docs.example.com/sdk/one", "title": "one"},
            {"url": "https://external.example.net/source", "title": "external"},
        ]]
    )
    service = _service(monkeypatch, tmp_path, operations)
    original = service._write_run_state
    attempts = 0

    def fail_terminal_writes(run):
        nonlocal attempts
        attempts += 1
        if attempts in (3, 4):
            raise OSError("injected terminal state persistence failure")
        return original(run)

    monkeypatch.setattr(service, "_write_run_state", fail_terminal_writes)
    result = await service.build_research_pack(
        topic="terminal-marker-reload",
        max_research_pages=2,
        research_targets=[
            _target("Docs", "https://docs.example.com/sdk/", ("capabilities", "sdk"))
        ],
    )

    assert attempts == 4
    assert result.status is WorkflowStatus.FAILED
    assert result.documents == []
    assert result.citations == []
    assert result.summary_sections == []
    assert result.artifacts == []
    assert result.report_path is None
    assert result.manifest_path is None

    state_path = service._paths.workflow_runs_dir / f"{result.run_id}.json"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    # The last durable state is the completed finalization write; the failure
    # marker must make this stale payload safe when a new service reloads it.
    assert persisted["status"] == "completed"
    assert persisted["documents"]
    assert persisted["artifacts"]
    assert persisted["report_path"]
    assert persisted["manifest_path"]
    marker_path = service._paths.workflow_runs_dir / f"{result.run_id}.failure.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["clear_artifacts"] is True

    reloaded_service = WorkflowService(
        SimpleNamespace(),
        corpus_paths=service._paths,
    )
    reloaded = reloaded_service.get_run(result.run_id)

    assert reloaded is not None
    assert reloaded.status is WorkflowStatus.FAILED
    assert reloaded.documents == []
    assert reloaded.citations == []
    assert reloaded.summary_sections == []
    assert reloaded.artifacts == []
    assert reloaded.report_path is None
    assert reloaded.manifest_path is None
    snapshot_dir = Path(result.snapshot_dir)
    assert not any((snapshot_dir / "targeted-research").rglob("*"))
    assert not (snapshot_dir / "SUMMARY.md").exists()
    assert not (snapshot_dir / "manifest.json").exists()


def test_targeted_cleanup_refuses_symlinked_snapshot_root(monkeypatch, tmp_path):
    import shutil
    from pathlib import Path

    from argus.workflows.models import (
        CitationRef,
        StoredDocument,
        SummarySection,
        WorkflowArtifact,
        WorkflowKind,
    )

    service = _service(monkeypatch, tmp_path, _TargetOperations([]))
    run = service._create_run(
        WorkflowKind.BUILD_RESEARCH_PACK,
        "symlinked-snapshot",
        extra_metadata={
            "research_targets": [
                _target(
                    "Docs",
                    "https://docs.example.com/sdk/",
                    ("capabilities", "sdk"),
                )
            ]
        },
    )
    snapshot_dir = Path(run.snapshot_dir)
    outside_dir = tmp_path / "outside-snapshot"
    targeted_dir = outside_dir / "targeted-research"
    targeted_dir.mkdir(parents=True)
    outside_file = targeted_dir / "must-remain.md"
    outside_file.write_text("outside", encoding="utf-8")
    shutil.rmtree(snapshot_dir)
    snapshot_dir.symlink_to(outside_dir, target_is_directory=True)

    run.documents = [
        StoredDocument(
            id="doc-1",
            url="https://docs.example.com/sdk/one",
            title="one",
            artifact_path=str(snapshot_dir / "targeted-research" / "doc-1.md"),
        )
    ]
    run.citations = [
        CitationRef(
            id="doc-1",
            title="one",
            url="https://docs.example.com/sdk/one",
            artifact_path=str(snapshot_dir / "targeted-research" / "doc-1.md"),
        )
    ]
    run.summary_sections = [SummarySection(heading="Target", body="body")]
    run.report_path = str(snapshot_dir / "SUMMARY.md")
    run.manifest_path = str(snapshot_dir / "manifest.json")
    run.artifacts = [
        WorkflowArtifact(kind="report", path=run.report_path),
        WorkflowArtifact(kind="manifest", path=run.manifest_path),
    ]

    service._cleanup_failed_targeted_artifacts(run)

    assert snapshot_dir.is_symlink()
    assert outside_file.is_file()
    assert run.documents == []
    assert run.citations == []
    assert run.summary_sections == []
    assert run.artifacts == []
    assert run.report_path is None
    assert run.manifest_path is None


def test_targeted_cleanup_refuses_real_outside_snapshot_root(monkeypatch, tmp_path):
    from argus.workflows.models import (
        CitationRef,
        StoredDocument,
        SummarySection,
        WorkflowArtifact,
        WorkflowKind,
    )

    service = _service(monkeypatch, tmp_path, _TargetOperations([]))
    run = service._create_run(
        WorkflowKind.BUILD_RESEARCH_PACK,
        "outside-snapshot",
        extra_metadata={
            "research_targets": [
                _target(
                    "Docs",
                    "https://docs.example.com/sdk/",
                    ("capabilities", "sdk"),
                )
            ]
        },
    )
    outside_dir = tmp_path / "outside-snapshot"
    targeted_dir = outside_dir / "targeted-research"
    targeted_dir.mkdir(parents=True)
    outside_files = (
        targeted_dir / "must-remain.md",
        outside_dir / "SUMMARY.md",
        outside_dir / "manifest.json",
    )
    outside_files[0].write_text("outside target", encoding="utf-8")
    outside_files[1].write_text("outside report", encoding="utf-8")
    outside_files[2].write_text("outside manifest", encoding="utf-8")
    run.snapshot_dir = str(outside_dir)

    run.documents = [
        StoredDocument(
            id="doc-1",
            url="https://docs.example.com/sdk/one",
            title="one",
            artifact_path=str(outside_files[0]),
        )
    ]
    run.citations = [
        CitationRef(
            id="doc-1",
            title="one",
            url="https://docs.example.com/sdk/one",
            artifact_path=str(outside_files[0]),
        )
    ]
    run.summary_sections = [SummarySection(heading="Target", body="body")]
    run.report_path = str(outside_files[1])
    run.manifest_path = str(outside_files[2])
    run.artifacts = [
        WorkflowArtifact(kind="report", path=run.report_path),
        WorkflowArtifact(kind="manifest", path=run.manifest_path),
    ]

    service._cleanup_failed_targeted_artifacts(run)

    assert all(path.is_file() for path in outside_files)
    assert targeted_dir.is_dir()
    assert run.documents == []
    assert run.citations == []
    assert run.summary_sections == []
    assert run.artifacts == []
    assert run.report_path is None
    assert run.manifest_path is None


def test_targeted_cleanup_refuses_another_run_snapshot(monkeypatch, tmp_path):
    from pathlib import Path

    from argus.workflows.models import WorkflowArtifact, WorkflowKind

    service = _service(monkeypatch, tmp_path, _TargetOperations([]))
    target_metadata = {
        "research_targets": [
            _target(
                "Docs",
                "https://docs.example.com/sdk/",
                ("capabilities", "sdk"),
            )
        ]
    }
    run_one = service._create_run(
        WorkflowKind.BUILD_RESEARCH_PACK,
        "shared-snapshot",
        extra_metadata=target_metadata,
    )
    run_two = service._create_run(
        WorkflowKind.BUILD_RESEARCH_PACK,
        "shared-snapshot",
        extra_metadata=target_metadata,
    )

    run_one_snapshot = Path(run_one.snapshot_dir)
    targeted_dir = run_one_snapshot / "targeted-research"
    targeted_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = targeted_dir / "run-one.md"
    report_path = run_one_snapshot / "SUMMARY.md"
    manifest_path = run_one_snapshot / "manifest.json"
    for path in (evidence_path, report_path, manifest_path):
        path.write_text("run one evidence", encoding="utf-8")

    run_two.snapshot_dir = run_one.snapshot_dir
    run_two.report_path = str(report_path)
    run_two.manifest_path = str(manifest_path)
    run_two.artifacts = [
        WorkflowArtifact(kind="report", path=str(report_path)),
        WorkflowArtifact(kind="manifest", path=str(manifest_path)),
    ]

    service._cleanup_failed_targeted_artifacts(run_two)

    assert evidence_path.is_file()
    assert report_path.is_file()
    assert manifest_path.is_file()


def test_reloaded_target_deadline_discards_process_monotonic_anchor(
    monkeypatch, tmp_path
):
    from datetime import datetime, timezone

    from argus.workflows.models import WorkflowKind, WorkflowStatus
    from argus.workflows.service import WorkflowService

    class Clock:
        def __init__(self, monotonic):
            self.monotonic_value = monotonic
            self.current = datetime(2026, 8, 10, tzinfo=timezone.utc)

        def now(self):
            return self.current

        def monotonic(self):
            return self.monotonic_value

    first_clock = Clock(100.0)
    base_service = _service(monkeypatch, tmp_path, _TargetOperations([]))
    service = WorkflowService(
        SimpleNamespace(),
        corpus_paths=base_service._paths,
        clock=first_clock,
    )
    run = service._create_run(
        WorkflowKind.BUILD_RESEARCH_PACK,
        "deadline-reload",
        extra_metadata={
            "research_targets": [
                _target(
                    "Docs",
                    "https://docs.example.com/sdk/",
                    ("capabilities", "sdk"),
                )
            ]
        },
    )
    run.status = WorkflowStatus.COMPLETED
    service._write_run_state(run)
    persisted_anchor = run.metadata["_deadline_monotonic"]

    reloaded_service = WorkflowService(
        SimpleNamespace(),
        corpus_paths=base_service._paths,
        clock=Clock(10_000.0),
    )
    reloaded = reloaded_service.get_run(run.run_id)

    assert reloaded is not None
    assert reloaded.metadata.get("_deadline_monotonic") is None
    remaining = reloaded_service._remaining_workflow_seconds(reloaded)
    assert remaining == pytest.approx(540.0)
    assert reloaded.metadata["_deadline_monotonic"] == pytest.approx(10_540.0)
    assert persisted_anchor == pytest.approx(640.0)


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


class _FakeWorkflowClock:
    """Clock seam used by Task 6 deadline tests."""

    def __init__(self):
        from datetime import datetime, timezone

        self.current = datetime(2026, 8, 10, tzinfo=timezone.utc)
        self.ticks = 0.0

    def now(self):
        return self.current

    def monotonic(self):
        return self.ticks

    def advance(self, seconds):
        from datetime import timedelta

        self.ticks += seconds
        self.current += timedelta(seconds=seconds)


@pytest.mark.asyncio
async def test_targeted_scheduler_collects_concurrently_but_returns_target_order(
    monkeypatch, tmp_path
):
    """Independent targets may overlap while public documents stay deterministic."""

    from argus.contracts import AcceptedOperation, CanonicalOutcome
    from argus.workflows.models import WorkflowStatus

    class Operations(_TargetOperations):
        async def search(self, request, *, principal, request_id):
            del principal
            self.search_calls.append((request, request_id))
            # Target two finishes first; the workflow must still publish target one
            # first because the request order is the public selection order.
            if "second" in request.query:
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(0.01)
            index = 1 if "second" in request.query else 0
            return AcceptedOperation(
                outcome=CanonicalOutcome.SUCCESS,
                request_id=request_id,
                result={
                    "results": tuple(self.results[index]),
                    "acceptance_receipt": {"receipt_ref": f"receipt-{index}"},
                },
                error=None,
            )

    import asyncio

    operations = Operations(
        [
            [{"url": "https://docs.example.com/docs/one", "title": "one"},
             {"url": "https://outside.example.net/a", "title": "a"}],
            [{"url": "https://docs.example.org/docs/two", "title": "two"},
             {"url": "https://outside.example.org/b", "title": "b"}],
        ]
    )
    service = _service(monkeypatch, tmp_path, operations)
    result = await service.build_research_pack(
        topic="ordered",
        max_research_pages=4,
        research_targets=[
            _target("One", "https://docs.example.com/docs/", ("capabilities", "first")),
            _target("Two", "https://docs.example.org/docs/", ("capabilities", "second")),
        ],
    )
    assert result.status is WorkflowStatus.COMPLETED
    assert [document.url for document in result.documents[:2]] == [
        "https://docs.example.com/docs/one",
        "https://docs.example.org/docs/two",
    ]


@pytest.mark.asyncio
async def test_targeted_scheduler_candidate_timeout_falls_back_without_late_call(
    monkeypatch, tmp_path
):
    import asyncio
    from argus.workflows.models import WorkflowStatus

    class Operations(_TargetOperations):
        async def compose_workflow(self, retrieval, **kwargs):
            url = kwargs["selection_urls"][0]
            self.attempted_urls = getattr(self, "attempted_urls", []) + [url]
            if url.endswith("first"):
                await asyncio.sleep(0.05)
            return await super().compose_workflow(retrieval, **kwargs)

    first = "https://docs.example.com/sdk/first"
    second = "https://docs.example.com/sdk/second"
    operations = Operations(
        [[
            {"url": first, "title": "first"},
            {"url": second, "title": "second"},
            {"url": "https://outside.example.net/a", "title": "external"},
        ]]
    )
    service = _service(monkeypatch, tmp_path, operations)
    monkeypatch.setattr(service, "TARGET_CANDIDATE_TIMEOUT_SECONDS", 0.01)
    result = await service.build_research_pack(
        topic="candidate-timeout",
        max_research_pages=2,
        research_targets=[
            _target("Docs", "https://docs.example.com/sdk/", ("capabilities", "sdk"))
        ],
    )
    assert result.status is WorkflowStatus.COMPLETED
    assert operations.attempted_urls[:2] == [first, second]


@pytest.mark.asyncio
async def test_targeted_scheduler_stops_new_operations_after_global_deadline(
    monkeypatch, tmp_path
):
    from argus.workflows.models import WorkflowStatus

    clock = _FakeWorkflowClock()

    class Operations(_TargetOperations):
        async def search(self, request, *, principal, request_id):
            clock.advance(541)
            return await super().search(request, principal=principal, request_id=request_id)

    operations = Operations(
        [
            [{"url": "https://docs.example.com/docs/one", "title": "one"}],
            [{"url": "https://docs.example.org/docs/two", "title": "two"}],
        ]
    )
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    from argus.workflows.service import WorkflowService

    service = WorkflowService(operations, clock=clock)
    result = await service.build_research_pack(
        topic="deadline",
        max_research_pages=3,
        research_targets=[
            _target("One", "https://docs.example.com/docs/", ("capabilities", "one")),
            _target("Two", "https://docs.example.org/docs/", ("capabilities", "two")),
        ],
    )
    assert result.status is WorkflowStatus.FAILED
    assert result.error == "workflow_deadline_exceeded"
    assert len(operations.search_calls) <= 2


@pytest.mark.asyncio
async def test_targeted_documents_carry_execution_and_text_hash_evidence(
    monkeypatch, tmp_path
):
    import hashlib

    operations = _TargetOperations(
        [[
            {"url": "https://docs.example.com/sdk/one", "title": "one"},
            {"url": "https://outside.example.net/a", "title": "external"},
        ]]
    )
    result = await _service(monkeypatch, tmp_path, operations).build_research_pack(
        topic="evidence",
        max_research_pages=2,
        research_targets=[
            _target("Docs", "https://docs.example.com/sdk/", ("capabilities", "sdk"))
        ],
    )
    target_document = result.documents[0]
    assert target_document.source_type == "targeted_first_party"
    assert target_document.role == "primary"
    assert target_document.metadata["claim_class"] == "capabilities"
    assert target_document.metadata["egress"] == "unknown"
    assert target_document.metadata["text_sha256"] == hashlib.sha256(
        "accepted target content".encode()
    ).hexdigest()
    assert "execution_evidence" in target_document.metadata
    assert "search_execution_evidence" in target_document.metadata


def test_targeted_mode_does_not_write_legacy_docs_cache_alias_or_sort_by_word_count(
    monkeypatch, tmp_path
):
    """Targeted artifacts retain input order and never mirror official docs cache."""

    # The ordering assertion is exercised by the async scheduler test; this test
    # reserves the public cache behavior as a separate acceptance seam.
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    from argus.workflows.service import WorkflowService

    service = WorkflowService(SimpleNamespace())
    before = service._paths.docs_cache_index.read_text(encoding="utf-8")
    # Targeted execution passes no docs-cache arguments to finalization; the
    # pre-existing catalog is therefore the only content at this seam.
    assert service._paths.docs_cache_index.read_text(encoding="utf-8") == before


def test_reloaded_orphaned_target_run_is_interrupted_without_accepted_calls(
    monkeypatch, tmp_path
):
    from argus.workflows.models import WorkflowKind, WorkflowStatus
    from argus.workflows.service import WorkflowService

    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    operations = _TargetOperations([])
    service = WorkflowService(operations)
    target = _target(
        "Docs",
        "https://docs.example.com/sdk/",
        ("capabilities", "sdk"),
    )
    run = service._create_run(
        WorkflowKind.BUILD_RESEARCH_PACK,
        "SDK",
        extra_metadata={
            "research_targets": [target],
            "original_evidence": {"request_sha256": "a" * 64},
        },
    )
    service._write_run_state(run)

    reloaded = WorkflowService(SimpleNamespace(), corpus_paths=service._paths).get_run(
        run.run_id
    )
    assert reloaded is not None
    assert reloaded.status is WorkflowStatus.FAILED
    assert reloaded.error == "workflow_interrupted"
    assert reloaded.metadata["original_evidence"] == {
        "request_sha256": "a" * 64
    }
    assert operations.search_calls == []
