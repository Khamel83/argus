"""Acceptance-v3 report/manifest closure tests.

These tests use an already accepted, synthetic workflow result.  No provider
or extraction call is needed to exercise the public closure boundary.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from argus.workflows.models import (
    CitationRef,
    StoredDocument,
    SummarySection,
    WorkflowKind,
    WorkflowResult,
    WorkflowStatus,
)
from argus.workflows.service import WorkflowService


def _service() -> WorkflowService:
    from types import SimpleNamespace

    return WorkflowService(SimpleNamespace())


def _target(name: str, prefix: str, requirements: list[tuple[str, str]]) -> dict:
    return {
        "name": name,
        "source_prefixes": [prefix],
        "requirements": [
            {"claim_class": claim_class, "query": query}
            for claim_class, query in requirements
        ],
    }


def _run(tmp_path: Path, requirement_count: int = 1) -> WorkflowResult:
    tmp_path.mkdir(parents=True, exist_ok=True)
    targets = [
        _target(
            "Docs",
            "https://docs.example.com/docs/",
            [
                (f"claim_{index}", f"requirement {index}")
                for index in range(requirement_count)
            ],
        )
    ]
    documents: list[StoredDocument] = []
    citations: list[CitationRef] = []
    requirement_rows: list[dict] = []
    for index in range(requirement_count):
        citation_id = f"S{index + 1}"
        url = f"https://docs.example.com/docs/page-{index}"
        text = f"Accepted observation for requirement {index}."
        source_path = tmp_path / f"{citation_id}.md"
        source_path.write_text(text, encoding="utf-8")
        retrieved_at = f"2026-08-09T12:{index:02d}:00+00:00"
        diagnostics = {
            "provider": "test",
            "extractor": "test-extractor",
            "status": "success",
            "result_count": 1,
            "timeout_source": None,
            "operation_latency_ms": 1,
            "cache_latency_ms": 1,
            "cache_state": "miss",
            "cache_age_ms": None,
            "cache_origin": "none",
            "spend_provenance": "not_applicable",
            "freshness_age_ms": None,
            "freshness_window": "2025-08-09/2026-08-09",
            "freshness_reason": "source date is absent",
            "free_profile_eligible": True,
            "egress": "residential",
            "machine": "test-machine",
            "source_type": "targeted_first_party",
        }
        metadata = {
            "target_name": "Docs",
            "claim_class": f"claim_{index}",
            "requirement_ref": f"target-0-requirement-{index}",
            "target_index": 0,
            "requirement_index": index,
            "artifact_disposition": "usable",
            "retrieved_at": retrieved_at,
            "source_date": None,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "provider": "test",
            "extractor": "test-extractor",
            "egress": "residential",
            "machine": "test-machine",
            "source_type": "targeted_first_party",
            "cache_state": "miss",
            "cache_age": None,
            "cache_origin": "none",
            "spend_provenance": "not_applicable",
            "freshness_window": "2025-08-09/2026-08-09",
            "freshness_reason": "source date is absent",
            "free_profile_eligible": True,
            "execution_diagnostics": [diagnostics],
            "execution_evidence": {
                "schema": "argus-execution-evidence-v1",
                "diagnostics": [diagnostics],
            },
        }
        documents.append(
            StoredDocument(
                id=citation_id,
                url=url,
                title=f"Page {index}",
                artifact_path=str(source_path),
                word_count=len(text.split()),
                domain="docs.example.com",
                role="primary",
                source_type="targeted_first_party",
                extractor="test-extractor",
                egress="residential",
                machine="test-machine",
                metadata=metadata,
            )
        )
        citations.append(
            CitationRef(
                id=citation_id,
                title=f"Page {index}",
                url=url,
                artifact_path=str(source_path),
                note="targeted_first_party",
            )
        )
        requirement_rows.append(
            {
                "target_index": 0,
                "requirement_index": index,
                "target_name": "Docs",
                "claim_class": f"claim_{index}",
                "requirement_ref": f"target-0-requirement-{index}",
            }
        )

    external_text = "Independent secondary observation."
    external_path = tmp_path / "external.md"
    external_path.write_text(external_text, encoding="utf-8")
    external_meta = dict(documents[-1].metadata)
    external_meta.update(
        {
            "target_name": None,
            "claim_class": "external-secondary",
            "requirement_ref": "external-0",
            "source_type": "external_research",
            "text_sha256": hashlib.sha256(external_text.encode()).hexdigest(),
            "source_text_sha256": hashlib.sha256(external_text.encode()).hexdigest(),
        }
    )
    documents.append(
        StoredDocument(
            id=f"S{requirement_count + 1}",
            url="https://independent.example.net/review",
            title="Independent Review",
            artifact_path=str(external_path),
            word_count=len(external_text.split()),
            domain="independent.example.net",
            role="external_research",
            source_type="external_research",
            extractor="test-extractor",
            egress="residential",
            machine="test-machine",
            metadata=external_meta,
        )
    )
    citations.append(
        CitationRef(
            id=f"S{requirement_count + 1}",
            title="Independent Review",
            url="https://independent.example.net/review",
            artifact_path=str(external_path),
            note="external_research",
        )
    )
    run = WorkflowResult(
        run_id="closure-run",
        kind=WorkflowKind.BUILD_RESEARCH_PACK,
        status=WorkflowStatus.PENDING,
        target="Managed research",
        created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        started_at=datetime(2026, 8, 9, 1, tzinfo=timezone.utc),
        snapshot_dir=str(tmp_path),
        documents=documents,
        citations=citations,
        summary_sections=[
            SummarySection(
                heading="Pack Composition",
                body="Each requirement is represented by an accepted source.",
                citation_ids=[item.id for item in citations],
            )
        ],
        metadata={
            "safe_start": True,
            "caller_identity": "mac-agents",
            "caller_label": "closure-test",
            "runtime": {
                "version": "1.6.4",
                "source_revision": "a" * 40,
                "image_identity": "sha256:" + "b" * 64,
                "deployment_identity": "deploy-closure",
            },
            "request_sha256": "c" * 64,
            "research_plan": {
                "contract_schema": "build-research-pack/v3",
                "free_only": True,
                "caller_identity": "mac-agents",
                "caller_label": "closure-test",
                "max_research_pages": requirement_count + 1,
                "targets": targets,
            },
            "targeted_research": {
                "requirement_count": requirement_count,
                "target_candidate_attempts": requirement_count,
                "target_document_count": requirement_count,
                "external_document_count": 1,
                "page_budget": requirement_count + 1,
                "requirements": requirement_rows,
            },
        },
    )
    return run


def test_completed_targeted_pack_projects_one_to_one_requirement_closure(tmp_path):
    service = _service()
    run = _run(tmp_path, requirement_count=2)

    service._finalize_run(run, title="Research Pack: Managed research", report_name="SUMMARY.md")

    manifest = json.loads(Path(run.manifest_path).read_text(encoding="utf-8"))
    plan = manifest["research_plan"]
    assert manifest["status"] == "completed"
    assert plan["targets"][0]["outcome"] == "covered"
    requirements = plan["targets"][0]["requirements"]
    assert len(requirements) == 2
    assert all(row["outcome"] == "artifact_acquired" for row in requirements)
    assert all(len(row["citation_ids"]) == 1 for row in requirements)
    assert all(len(row["selected_urls"]) == 1 for row in requirements)
    assert manifest["closure_audit"] == {
        "report_citation_count": 3,
        "unresolved_citation_count": 0,
        "unresolved_url_count": 0,
        "missing_requirement_citation_count": 0,
    }
    assert len(manifest["claim_evidence_matrix"]) == 2

    report = Path(run.report_path).read_text(encoding="utf-8")
    assert "## Claim Evidence Matrix" in report
    assert "[S1]" in report and "[S2]" in report
    assert "observed_live_undated" in report


@pytest.mark.parametrize("requirement_count", (1, 16))
def test_requirement_closure_counts_are_input_driven(tmp_path, requirement_count):
    service = _service()
    run = _run(tmp_path / str(requirement_count), requirement_count=requirement_count)

    service._finalize_run(run, title="Research Pack: Managed research", report_name="SUMMARY.md")
    manifest = json.loads(Path(run.manifest_path).read_text(encoding="utf-8"))
    target = manifest["research_plan"]["targets"][0]
    budget = manifest["research_plan"]["page_budget"]
    assert len(target["requirements"]) == requirement_count
    assert budget["target_documents"] == requirement_count
    assert budget["external_documents"] == 1
    assert budget["total_documents"] == requirement_count + 1
    assert budget["maximum"] == requirement_count + 1


def test_freshness_window_is_deterministic_and_missing_dates_are_undated(tmp_path):
    service = _service()
    for source_date, expected in [
        ("2025-08-09", "dated_current"),
        ("2026-08-09", "dated_current"),
        ("2025-08-08", "stale"),
        ("2026-08-10", "unknown"),
        ("not-a-date", "unknown"),
        (None, "observed_live_undated"),
    ]:
        run_dir = tmp_path / (expected + str(source_date))
        run = _run(run_dir, requirement_count=1)
        run.documents[0].metadata["source_date"] = source_date
        service._finalize_run(run, title="Research Pack: Managed research", report_name="SUMMARY.md")
        manifest = json.loads(Path(run.manifest_path).read_text(encoding="utf-8"))
        assert manifest["research_plan"]["targets"][0]["requirements"][0]["freshness"] == expected


def test_failed_targeted_run_is_status_only_and_does_not_publish_artifacts(tmp_path):
    service = _service()
    run = _run(tmp_path, requirement_count=1)
    run.citations.clear()

    with pytest.raises(ValueError, match="closure"):
        service._finalize_run(run, title="Research Pack: Managed research", report_name="SUMMARY.md")
    assert run.status is not WorkflowStatus.COMPLETED
    assert run.report_path is None
    assert run.manifest_path is None
    assert not (tmp_path / "SUMMARY.md").exists()
    assert not (tmp_path / "manifest.json").exists()


def test_public_projection_redacts_paths_and_caps_public_strings(tmp_path):
    service = _service()
    run = _run(tmp_path, requirement_count=1)
    run.documents[0].title = "/private/path\n" + ("x" * 2_000)
    run.documents[0].metadata["lead_text"] = "Authorization: Bearer supersecret"
    service._finalize_run(run, title="Research Pack: Managed research", report_name="SUMMARY.md")
    payload = Path(run.manifest_path).read_text(encoding="utf-8")
    assert str(tmp_path) not in payload
    assert "Authorization: Bearer" not in payload
    assert "supersecret" not in payload
    assert "private/path" not in payload
    assert len(payload.encode("utf-8")) < 4 * 1024 * 1024

    report = Path(run.report_path).read_text(encoding="utf-8")
    assert str(tmp_path) not in report
    assert "private/path" not in report


def test_targeted_closure_recomputes_source_hash_from_artifact(tmp_path):
    service = _service()
    run = _run(tmp_path, requirement_count=1)
    run.documents[0].metadata["source_text_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="closure"):
        service._finalize_run(
            run, title="Research Pack: Managed research", report_name="SUMMARY.md"
        )
    assert run.report_path is None
    assert run.manifest_path is None


def test_targeted_closure_rejects_missing_source_artifact(tmp_path):
    service = _service()
    run = _run(tmp_path, requirement_count=1)
    Path(run.documents[0].artifact_path).unlink()

    with pytest.raises(ValueError, match="closure"):
        service._finalize_run(
            run, title="Research Pack: Managed research", report_name="SUMMARY.md"
        )
    assert run.report_path is None
    assert run.manifest_path is None


def test_targeted_closure_requires_bounded_execution_diagnostics(tmp_path):
    service = _service()
    run = _run(tmp_path, requirement_count=1)
    run.documents[0].metadata["execution_diagnostics"] = [
        {
            "provider": "test",
            "extractor": "test-extractor",
            "status": "success",
            "junk": "Authorization: Bearer should-not-be-accepted",
        }
    ]
    run.documents[0].metadata["execution_evidence"] = {
        "diagnostics": run.documents[0].metadata["execution_diagnostics"]
    }

    with pytest.raises(ValueError, match="provenance diagnostics"):
        service._finalize_run(
            run, title="Research Pack: Managed research", report_name="SUMMARY.md"
        )
    assert run.report_path is None
    assert run.manifest_path is None


def test_targeted_closure_accepts_aware_receipt_datetime(tmp_path):
    service = _service()
    run = _run(tmp_path, requirement_count=1)
    run.documents[0].metadata["execution_evidence"]["persistence"] = {
        "availability": "available",
        "source": "acceptance_receipt",
        "accepted_at": datetime(2026, 8, 9, 12, tzinfo=timezone.utc),
    }

    service._finalize_run(
        run, title="Research Pack: Managed research", report_name="SUMMARY.md"
    )
    assert run.status is WorkflowStatus.COMPLETED


def test_targeted_closure_rejects_naive_receipt_datetime(tmp_path):
    service = _service()
    run = _run(tmp_path, requirement_count=1)
    run.documents[0].metadata["execution_evidence"]["persistence"] = {
        "availability": "available",
        "source": "acceptance_receipt",
        "accepted_at": datetime(2026, 8, 9, 12),
    }

    with pytest.raises(ValueError, match="provenance diagnostics"):
        service._finalize_run(
            run, title="Research Pack: Managed research", report_name="SUMMARY.md"
        )


def test_source_date_parser_rejects_trailing_junk(tmp_path):
    service = _service()
    run = _run(tmp_path, requirement_count=1)
    run.documents[0].metadata["source_date"] = "2026-08-09 trailing-junk"

    service._finalize_run(
        run, title="Research Pack: Managed research", report_name="SUMMARY.md"
    )
    manifest = json.loads(Path(run.manifest_path).read_text(encoding="utf-8"))
    source = manifest["sources"][0]
    assert source["source_date"] is None
    assert source["freshness"] == "unknown"


def test_targeted_closure_rejects_non_public_external_url(tmp_path):
    service = _service()
    run = _run(tmp_path, requirement_count=1)
    external = run.documents[-1]
    external.url = "file:///tmp/not-public"
    run.citations[-1].url = external.url

    with pytest.raises(ValueError, match="closure"):
        service._finalize_run(
            run, title="Research Pack: Managed research", report_name="SUMMARY.md"
        )
    assert run.report_path is None
    assert run.manifest_path is None


def test_targeted_closure_audits_summary_urls_and_citation_ids(tmp_path):
    service = _service()
    run = _run(tmp_path, requirement_count=1)
    run.summary_sections[0].body = (
        "Unsupported https://unaccepted.example.invalid/item [S999]"
    )

    with pytest.raises(ValueError, match="closure"):
        service._finalize_run(
            run, title="Research Pack: Managed research", report_name="SUMMARY.md"
        )
    assert run.report_path is None
    assert run.manifest_path is None


def test_targeted_closure_rejects_overlong_citation_ids(tmp_path):
    service = _service()
    run = _run(tmp_path, requirement_count=1)
    long_id = "S" + ("x" * 150)
    run.documents[-1].id = long_id
    run.citations[-1].id = long_id
    run.summary_sections[0].citation_ids[-1] = long_id

    with pytest.raises(ValueError, match="citation"):
        service._finalize_run(
            run, title="Research Pack: Managed research", report_name="SUMMARY.md"
        )
    assert run.report_path is None
    assert run.manifest_path is None
