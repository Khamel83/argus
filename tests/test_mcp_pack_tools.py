"""Tests for machine-readable MCP research-pack output (issue #19)."""

import json
from datetime import datetime

from argus.mcp.tools import _serialize_workflow_json
from argus.workflows.models import (
    StoredDocument,
    WorkflowArtifact,
    WorkflowKind,
    WorkflowResult,
    WorkflowStatus,
)


def _run(tmp_path):
    report = tmp_path / "SUMMARY.md"
    report.write_text("# Summary\n", encoding="utf-8")
    doc = tmp_path / "doc1.md"
    doc.write_text("content", encoding="utf-8")
    return WorkflowResult(
        run_id="run-123",
        kind=WorkflowKind.BUILD_RESEARCH_PACK,
        status=WorkflowStatus.COMPLETED,
        target="fastapi",
        created_at=datetime(2026, 7, 5),
        snapshot_dir=str(tmp_path),
        report_path=str(report),
        manifest_path=str(tmp_path / "manifest.json"),
        artifacts=[WorkflowArtifact(kind="report", path=str(report))],
        documents=[
            StoredDocument(
                id="d1", url="https://x.test", title="Doc 1", artifact_path=str(doc)
            )
        ],
    )


def test_serialize_workflow_json_shape(tmp_path):
    payload = json.loads(_serialize_workflow_json(_run(tmp_path)))
    assert payload["run_id"] == "run-123"
    assert payload["status"] == "completed"
    assert payload["report_path"].endswith("SUMMARY.md")
    paths = {f["path"] for f in payload["files"]}
    assert str(tmp_path / "SUMMARY.md") in paths
    assert str(tmp_path / "doc1.md") in paths
    for f in payload["files"]:
        assert isinstance(f["bytes"], int)


def test_serialize_workflow_json_error_run(tmp_path):
    run = _run(tmp_path)
    run.error = "boom"
    run.status = WorkflowStatus.FAILED
    payload = json.loads(_serialize_workflow_json(run))
    assert payload["error"] == "boom"
    assert payload["status"] == "failed"
