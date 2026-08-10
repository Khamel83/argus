"""Safe research-pack status and artifact projections."""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from argus.workflows.models import (
    CitationRef,
    StoredDocument,
    WorkflowArtifact,
    WorkflowKind,
    WorkflowResult,
    WorkflowStatus,
)
from argus.workflows.service import WorkflowService


def _service() -> WorkflowService:
    return WorkflowService(SimpleNamespace())


def _run(tmp_path, *, status=WorkflowStatus.COMPLETED):
    tmp_path.mkdir(parents=True, exist_ok=True)
    report = tmp_path / "SUMMARY.md"
    report.write_text("# Report\n" + ("a" * 32), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"status": status.value}), encoding="utf-8")
    document = tmp_path / "s1.md"
    document.write_text("source", encoding="utf-8")
    run = WorkflowResult(
        run_id="run-safe",
        kind=WorkflowKind.BUILD_RESEARCH_PACK,
        status=status,
        target="Parallel",
        created_at=datetime(2026, 8, 9, 1, 0),
        started_at=datetime(2026, 8, 9, 1, 0, 1),
        finished_at=(
            datetime(2026, 8, 9, 1, 0, 2)
            if status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}
            else None
        ),
        status_url="/api/workflows/run-safe",
        snapshot_dir=str(tmp_path),
        report_path=str(report),
        manifest_path=str(manifest),
        artifacts=[
            WorkflowArtifact("report", str(report), "Human report"),
            WorkflowArtifact("manifest", str(manifest), "Machine manifest"),
        ],
        documents=[
            StoredDocument(
                id="S1",
                url="https://docs.example.com/start",
                title="Docs",
                artifact_path=str(document),
                domain="docs.example.com",
                role="official_doc",
                source_type="official_docs",
                metadata={"artifact_disposition": "partial"},
            )
        ],
        citations=[
            CitationRef(
                id="S1",
                title="Docs",
                url="https://docs.example.com/start",
                artifact_path=str(document),
                note="official_docs; artifact_disposition=partial",
            )
        ],
        metadata={"caller_identity": "mac-agents", "cost_state": "uncertain"},
    )
    return run


def test_safe_status_projection_omits_local_paths_and_reports_artifact_hashes(tmp_path):
    service = _service()
    run = _run(tmp_path)

    payload = service.get_public_status(run)

    assert payload["run_id"] == "run-safe"
    assert payload["status"] == "completed"
    assert payload["cost_state"] == "uncertain"
    assert payload["source_count"] == 1
    assert payload["domain_count"] == 1
    assert payload["citations"][0]["disposition"] == "partial"
    assert payload["citations"][0]["evidence_ids"] == ["S1"]
    assert {artifact["kind"] for artifact in payload["artifacts"]} == {
        "report",
        "manifest",
    }
    assert all(artifact["sha256"] for artifact in payload["artifacts"])
    assert "snapshot_dir" not in payload
    assert "report_path" not in payload
    assert str(tmp_path) not in json.dumps(payload)


def test_bounded_artifact_read_returns_hash_and_pagination_metadata(tmp_path):
    service = _service()
    run = _run(tmp_path)

    first = service.read_artifact(run, "report", offset=0, max_bytes=8)
    second = service.read_artifact(
        run,
        "report",
        offset=first["next_offset"],
        max_bytes=256 * 1024,
    )

    assert first["content"] == "# Report"
    assert first["bytes_returned"] == 8
    assert first["truncated"] is True
    assert first["next_offset"] == 8
    assert first["sha256"] == second["sha256"]
    assert second["offset"] == 8
    assert second["truncated"] is False


def test_bounded_artifact_read_never_splits_utf8_codepoints(tmp_path):
    service = _service()
    run = _run(tmp_path)
    report = tmp_path / "SUMMARY.md"
    report.write_text("a😀bcdef", encoding="utf-8")

    page = service.read_artifact(run, "report", offset=2, max_bytes=4)
    follow_up = service.read_artifact(
        run,
        "report",
        offset=page["next_offset"],
        max_bytes=256 * 1024,
    )

    assert page["content"] == "😀"
    assert page["content"].encode("utf-8").decode("utf-8") == page["content"]
    assert page["next_offset"] == 5
    assert follow_up["content"] == "bcdef"


def test_pending_run_cannot_read_artifact(tmp_path):
    service = _service()
    run = _run(tmp_path, status=WorkflowStatus.RUNNING)

    with pytest.raises(service.ArtifactNotReady):
        service.read_artifact(run, "report")


def test_unregistered_artifact_and_containment_fail_closed(tmp_path):
    service = _service()
    run = _run(tmp_path)

    with pytest.raises(service.ArtifactNotFound):
        service.read_artifact(run, "summary")

    run.artifacts[0].path = str(tmp_path.parent / "outside.md")
    (tmp_path.parent / "outside.md").write_text("secret", encoding="utf-8")
    with pytest.raises(service.ArtifactUnavailable):
        service.read_artifact(run, "report")


@pytest.mark.asyncio
async def test_finalization_failure_does_not_publish_completed_report(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    from tests.test_workflows import _service

    service, _ = _service()
    original = service._atomic_write_text

    def fail_manifest(path, content):
        if path.name == "manifest.json":
            raise OSError("injected manifest failure")
        return original(path, content)

    monkeypatch.setattr(service, "_atomic_write_text", fail_manifest)
    result = await service.build_research_pack(
        topic="Example SDK", max_research_pages=1
    )

    assert result.status is WorkflowStatus.FAILED
    if result.report_path:
        report = __import__("pathlib").Path(result.report_path)
        if report.exists():
            assert "- Status: completed" not in report.read_text(encoding="utf-8")


def _route_client(run, runtime=None):
    app = FastAPI()
    service = _service()
    service._runs[run.run_id] = run
    app.state.get_workflows = lambda: service
    if runtime is not None:
        app.state.operational_status = runtime
    from argus.api.routes_workflows import router

    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_authenticated_status_and_artifact_routes_are_path_free(tmp_path):
    run = _run(tmp_path)

    class Runtime:
        def full_status(self):
            return {
                "build": {
                    "version": "1.6.3",
                    "source_revision": "a" * 40,
                },
                "deployment": {"deployment_id": "deploy-42"},
            }

    client = _route_client(run, Runtime())

    status = client.get(f"/api/workflows/{run.run_id}/status")
    artifact = client.get(
        f"/api/workflows/{run.run_id}/artifacts/report",
        params={"offset": 0, "max_bytes": 8},
    )

    assert status.status_code == 200
    assert status.json()["artifacts"][0]["sha256"]
    assert status.json()["runtime"] == {
        "version": "1.6.3",
        "source_revision": "a" * 40,
        "image_identity": "unknown",
        "deployment_identity": "deploy-42",
    }
    assert "snapshot_dir" not in status.json()
    assert str(tmp_path) not in status.text
    assert artifact.status_code == 200
    assert artifact.json()["bytes_returned"] == 8
    assert "path" not in artifact.json()


def test_artifact_routes_map_unknown_pending_and_invalid_requests(tmp_path):
    run = _run(tmp_path)
    client = _route_client(run)

    assert client.get("/api/workflows/missing/status").status_code == 404
    assert (
        client.get(f"/api/workflows/{run.run_id}/artifacts/summary").status_code == 404
    )

    pending = _run(tmp_path / "pending", status=WorkflowStatus.RUNNING)
    pending_client = _route_client(pending)
    assert (
        pending_client.get(
            f"/api/workflows/{pending.run_id}/artifacts/report"
        ).status_code
        == 409
    )

    assert (
        client.get(
            f"/api/workflows/{run.run_id}/artifacts/report",
            params={"max_bytes": 256 * 1024 + 1},
        ).status_code
        == 422
    )
