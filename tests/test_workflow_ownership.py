"""Workflow owner, singleton-authority, and artifact-publication contracts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from argus.api.routes_workflows import router
from argus.api.main import _build_workflow_provider
from argus.workflows.models import (
    WorkflowArtifact,
    WorkflowKind,
    WorkflowResult,
    WorkflowStatus,
)
from argus.workflows.service import (
    WorkflowAuthorityUnavailable,
    WorkflowOwnerMismatch,
    WorkflowOwnerUnavailable,
    WorkflowService,
)


def _paths(tmp_path: Path):
    from argus.corpus.paths import CorpusPaths

    data = tmp_path / "data"
    return CorpusPaths(
        data_root=data,
        docs_root=data / "docs",
        docs_cache_dir=data / "docs" / "cache",
        docs_cache_index=data / "docs" / "cache" / ".index.md",
        research_dir=data / "docs" / "research",
        workflow_runs_dir=data / "workflows" / "runs",
        snapshots_dir=data / "snapshots",
        imports_dir=data / "imports",
    ).ensure()


def _run(tmp_path: Path, *, owner: str = "owner-a", published: bool = True):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir(parents=True)
    report = snapshot / "SUMMARY.md"
    manifest = snapshot / "manifest.json"
    if published:
        report.write_text("# report\n", encoding="utf-8")
        manifest.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    return WorkflowResult(
        run_id="owner-run",
        kind=WorkflowKind.BUILD_RESEARCH_PACK,
        status=WorkflowStatus.COMPLETED,
        target="Argus",
        created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 2, 0, 1, tzinfo=timezone.utc),
        status_url="/api/workflows/owner-run/status",
        snapshot_dir=str(snapshot),
        report_path=str(report),
        manifest_path=str(manifest),
        artifacts=[
            WorkflowArtifact("report", str(report), "Report"),
            WorkflowArtifact("manifest", str(manifest), "Manifest"),
        ],
        metadata={
            "caller_identity": owner,
            "caller_label": "display-label",
            "owner_principal": owner,
            "token": "must-not-escape",
        },
        owner_principal=owner,
    )


def _app(service: WorkflowService) -> FastAPI:
    app = FastAPI()
    app.state.get_workflows = lambda: service

    @app.middleware("http")
    async def inject_test_identity(request, call_next):
        request.state.caller_identity = request.headers.get(
            "X-Test-Principal", "owner-a"
        )
        return await call_next(request)

    app.include_router(router, prefix="/api")
    return app


def test_owner_is_persisted_and_reloaded_and_never_uses_display_label(tmp_path):
    paths = _paths(tmp_path)
    service = WorkflowService(SimpleNamespace(), corpus_paths=paths)
    run = service._create_run(
        WorkflowKind.BUILD_RESEARCH_PACK,
        "Argus",
        caller_identity="owner-a",
        caller_label="attacker-controlled-label",
        extra_metadata={"owner_principal": "attacker"},
    )

    assert run.owner_principal == "owner-a"
    assert run.metadata["owner_principal"] == "owner-a"
    assert run.metadata["caller_label"] == "attacker-controlled-label"
    service._write_run_state(run)

    reloaded = WorkflowService(SimpleNamespace(), corpus_paths=paths)
    loaded = reloaded.get_run(run.run_id, principal="owner-a")
    assert loaded is not None
    assert loaded.owner_principal == "owner-a"
    assert reloaded.get_run(run.run_id, principal="attacker") is None


def test_every_read_checks_owner_before_returning_local_artifacts(tmp_path):
    run = _run(tmp_path)
    service = WorkflowService(SimpleNamespace())
    service._runs[run.run_id] = run

    with pytest.raises(WorkflowOwnerMismatch):
        service.get_public_status(run, principal="other-owner")
    with pytest.raises(WorkflowOwnerMismatch):
        service.read_artifact(run, "report", principal="other-owner")
    with pytest.raises(WorkflowOwnerMismatch):
        service.read_artifact(run, "manifest", principal="other-owner")
    assert service.get_run(run.run_id, principal="other-owner") is None

    status = service.get_public_status(run, principal="owner-a")
    assert status["run_id"] == run.run_id
    assert service.read_artifact(run, "report", principal="owner-a")["content"]


def test_http_owner_mismatch_is_bounded_not_found_and_legacy_response_is_path_free(
    tmp_path,
):
    run = _run(tmp_path)
    service = WorkflowService(SimpleNamespace())
    service._runs[run.run_id] = run
    client = TestClient(_app(service))

    denied_status = client.get(
        f"/api/workflows/{run.run_id}/status",
        headers={"X-Test-Principal": "other-owner"},
    )
    denied_artifact = client.get(
        f"/api/workflows/{run.run_id}/artifacts/report",
        headers={"X-Test-Principal": "other-owner"},
    )
    assert denied_status.status_code == 404
    assert denied_artifact.status_code == 404
    assert str(tmp_path) not in denied_status.text
    assert str(tmp_path) not in denied_artifact.text

    allowed = client.get(
        f"/api/workflows/{run.run_id}",
        headers={"X-Test-Principal": "owner-a"},
    )
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["snapshot_dir"] == ""
    assert payload["report_path"] is None
    assert payload["manifest_path"] is None
    assert payload["documents"] == []
    assert payload["metadata"] == {}
    assert str(tmp_path) not in allowed.text


def test_owner_unavailable_is_typed_unready_and_unpublished_is_typed_conflict(
    tmp_path,
):
    run = _run(tmp_path, published=False)
    service = WorkflowService(SimpleNamespace())
    service._runs[run.run_id] = run
    client = TestClient(_app(service))

    unpublished = client.get(
        f"/api/workflows/{run.run_id}/artifacts/report",
        headers={"X-Test-Principal": "owner-a"},
    )
    assert unpublished.status_code == 409
    assert unpublished.json() == {"detail": "Workflow artifact is not ready"}

    unavailable_service = WorkflowService(SimpleNamespace(), authority_available=False)
    unavailable = TestClient(_app(unavailable_service)).get(
        "/api/workflows/owner-run/status",
        headers={"X-Test-Principal": "owner-a"},
    )
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "Workflow authority unavailable"}


def test_singleton_provider_keeps_non_primary_production_process_unavailable(
    monkeypatch,
):
    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_NODE_ROLE", "caller")
    provider = _build_workflow_provider(lambda: SimpleNamespace())

    first = provider()
    second = provider()
    assert first is second
    assert first._authority_role == "caller"
    with pytest.raises(WorkflowAuthorityUnavailable):
        first.get_run("owner-run", principal="owner-a")


def test_legacy_states_without_a_verifiable_owner_are_unready(tmp_path):
    run = _run(tmp_path)
    run.owner_principal = None
    run.metadata.pop("owner_principal")
    run.metadata.pop("caller_identity")
    service = WorkflowService(SimpleNamespace())
    service._runs[run.run_id] = run

    with pytest.raises(WorkflowOwnerUnavailable):
        service.get_public_status(run, principal="owner-a")
