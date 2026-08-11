"""Safe research-pack status and artifact projections."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from argus.contracts import AcceptedOperation, CanonicalOutcome
from argus.operations.accepted import _operation_error
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


def test_target_failure_codes_are_not_rewritten_as_legacy_composition_codes():
    assert WorkflowService._target_authority_failure("unready") is True
    assert WorkflowService._target_authority_failure("persistence_failed") is True
    assert WorkflowService._target_authority_failure("contract_error") is True
    assert WorkflowService._target_authority_failure(
        "workflow_required_target_unready"
    ) is False


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
    assert payload["status_url"] == "/api/workflows/run-safe/status"
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


def test_bounded_artifact_read_rejects_limit_smaller_than_first_codepoint(tmp_path):
    service = _service()
    run = _run(tmp_path)
    (tmp_path / "SUMMARY.md").write_text("😀 tail", encoding="utf-8")

    with pytest.raises(service.ArtifactRange):
        service.read_artifact(run, "report", offset=0, max_bytes=1)

    with pytest.raises(service.ArtifactRange):
        service.read_artifact(run, "report", offset=1, max_bytes=2)


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
    status = _route_client(result).get(f"/api/workflows/{result.run_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "failed"
    manifest = next(
        item for item in status.json()["artifacts"] if item["kind"] == "manifest"
    )
    assert manifest["available"] is False


@pytest.mark.asyncio
async def test_late_state_write_failure_persists_failed_reloadable_run(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    from tests.test_workflows import _service

    service, _ = _service()
    paths = service._paths
    original = service._write_run_state
    calls = 0

    def fail_terminal_write(run):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected terminal state failure")
        return original(run)

    monkeypatch.setattr(service, "_write_run_state", fail_terminal_write)
    result = await service.build_research_pack(
        topic="Example SDK", max_research_pages=1
    )

    assert result.status is WorkflowStatus.FAILED
    reloaded_service = WorkflowService(SimpleNamespace(), corpus_paths=paths)
    reloaded = reloaded_service.get_run(result.run_id)
    assert reloaded is not None
    assert reloaded.status is WorkflowStatus.FAILED


@pytest.mark.asyncio
async def test_handler_failure_with_terminal_write_failure_persists_failed_state(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    from tests.test_workflows import _service

    service, _ = _service()
    paths = service._paths
    run = service._create_run(WorkflowKind.BUILD_RESEARCH_PACK, "Example SDK")
    original = service._write_run_state
    calls = 0

    def fail_terminal_write(run):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected failed-state write")
        return original(run)

    async def fail_handler(run):
        del run
        raise ValueError("internal detail must not escape")

    monkeypatch.setattr(service, "_write_run_state", fail_terminal_write)
    result = await service._execute_run(run.run_id, fail_handler)

    assert result.status is WorkflowStatus.FAILED
    reloaded = WorkflowService(SimpleNamespace(), corpus_paths=paths).get_run(
        result.run_id
    )
    assert reloaded is not None
    assert reloaded.status is WorkflowStatus.FAILED
    assert reloaded.error == "ValueError"


@pytest.mark.asyncio
async def test_real_terminal_artifacts_are_path_free_and_hash_truthful(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    from tests.test_workflows import _service

    service, _ = _service()
    result = await service.build_research_pack(
        topic="Example SDK", max_research_pages=1
    )

    assert result.status is WorkflowStatus.COMPLETED
    status = service.get_public_status(result)
    assert status["status"] == "completed"
    assert status["status_url"].endswith("/status")
    for timestamp_key in ("created_at", "started_at", "finished_at"):
        timestamp = datetime.fromisoformat(status[timestamp_key])
        assert timestamp.tzinfo is not None
        assert timestamp.utcoffset() == timezone.utc.utcoffset(timestamp)
    for artifact in status["artifacts"]:
        assert artifact["available"] is True
        page = service.read_artifact(result, artifact["kind"], max_bytes=256 * 1024)
        assert page["bytes_returned"] == artifact["size_bytes"]
        assert page["sha256"] == artifact["sha256"]
        assert page["bytes_returned"] <= 256 * 1024
        assert str(tmp_path) not in page["content"]

    manifest = service.read_artifact(result, "manifest", max_bytes=256 * 1024)[
        "content"
    ]
    manifest_payload = json.loads(manifest)
    assert manifest_payload["status"] == "completed"
    for timestamp_key in ("created_at", "started_at", "finished_at"):
        timestamp = datetime.fromisoformat(manifest_payload[timestamp_key])
        assert timestamp.tzinfo is not None
        assert timestamp.utcoffset() == timezone.utc.utcoffset(timestamp)
    report_entry = next(
        item for item in manifest_payload["artifacts"] if item["kind"] == "report"
    )
    assert report_entry["available"] is True
    assert report_entry["size_bytes"] > 0
    assert report_entry["sha256"]
    manifest_entry = next(
        item for item in manifest_payload["artifacts"] if item["kind"] == "manifest"
    )
    assert manifest_entry["available"] is True


def test_terminal_artifacts_are_path_free(tmp_path):
    service = _service()
    run = _run(tmp_path)
    service._finalize_run(run, title="Safe report", report_name="SUMMARY.md")

    report = service.read_artifact(run, "report", max_bytes=256 * 1024)["content"]
    manifest = service.read_artifact(run, "manifest", max_bytes=256 * 1024)["content"]

    assert str(tmp_path) not in report
    assert str(tmp_path) not in manifest
    assert "snapshot_dir" not in manifest
    assert "report_path" not in manifest
    assert "manifest_path" not in manifest
    assert "artifact_path" not in manifest
    payload = json.loads(manifest)

    def assert_safe_keys(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                assert key not in {
                    "snapshot_dir",
                    "report_path",
                    "manifest_path",
                    "artifact_path",
                    "path",
                }
                assert not key.endswith("_path")
                assert_safe_keys(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_safe_keys(nested)

    assert_safe_keys(payload)


def test_invalid_runtime_metadata_fails_closed(tmp_path):
    service = _service()
    run = _run(tmp_path)
    run.metadata["runtime"] = {
        "build": {
            "version": "/tmp/version-secret",
            "source_revision": "not-a-revision",
            "image_identity": "token=secret",
        },
        "deployment": {"deployment_id": "/tmp/deployment-secret"},
    }

    assert service.get_public_status(run)["runtime"] == {
        "version": "unknown",
        "source_revision": "unknown",
        "image_identity": "unknown",
        "deployment_identity": "unknown",
    }


@pytest.mark.parametrize(
    "image_identity",
    [
        "argus:latest",
        "docker.io/khamel83/argus@sha256:" + "a" * 64,
        "ghcr.io/khamel83/argus@sha256:" + "a" * 63,
    ],
)
def test_runtime_image_projection_rejects_noncanonical_identity(
    tmp_path, image_identity
):
    service = _service()
    run = _run(tmp_path)

    payload = service.get_public_status(
        run,
        runtime={"build": {"image_identity": image_identity}},
    )

    assert payload["runtime"]["image_identity"] == "unknown"


def test_status_projection_reports_truthful_evidence_and_runtime_metrics(tmp_path):
    service = _service()
    run = _run(tmp_path)
    second = tmp_path / "s2.md"
    second.write_text("second source", encoding="utf-8")
    third = tmp_path / "s3.md"
    third.write_text("third source", encoding="utf-8")
    run.documents.extend(
        [
            StoredDocument(
                id="S2",
                url="https://docs.example.com/other",
                title="Other docs",
                artifact_path=str(second),
                domain="docs.example.com",
                role="source",
                source_type="web",
            ),
            StoredDocument(
                id="S3",
                url="https://www.example.net/official",
                title="Official source",
                artifact_path=str(third),
                domain="www.example.net",
                role="official",
                source_type="official",
            ),
        ]
    )
    run.metadata.update(
        {
            "composition": {
                "outcome": "degraded",
                "degraded_artifact_refs": ["A2"],
                "rejected_extraction_refs": ["E3"],
            },
            "partial_reasons": ["source_text_incomplete"],
            "degraded_reasons": ["provider_degraded"],
            "cost_state": "uncertain",
        }
    )

    payload = service.get_public_status(
        run,
        runtime={
            "build": {
                "version": "1.6.4",
                "source_revision": "b" * 40,
                "image_identity": "sha256:" + "c" * 64,
            },
            "deployment": {"deployment_id": "deploy-43"},
        },
    )

    assert payload["source_count"] == 3
    assert payload["domain_count"] == 2
    assert payload["primary_source_count"] == 2
    assert "partial_artifact:S1" in payload["partial_reasons"]
    assert "source_text_incomplete" in payload["partial_reasons"]
    assert "workflow_composition_degraded" in payload["degraded_reasons"]
    assert "degraded_artifacts_present" in payload["degraded_reasons"]
    assert "rejected_extractions_present" in payload["degraded_reasons"]
    assert "provider_degraded" in payload["degraded_reasons"]
    assert payload["cost_state"] == "uncertain"
    assert payload["runtime"] == {
        "version": "1.6.4",
        "source_revision": "b" * 40,
        "image_identity": "sha256:" + "c" * 64,
        "deployment_identity": "deploy-43",
    }


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
                    "version": "1.6.4",
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
        "version": "1.6.4",
        "source_revision": "a" * 40,
        "image_identity": "unknown",
        "deployment_identity": "deploy-42",
    }
    assert "snapshot_dir" not in status.json()
    assert str(tmp_path) not in status.text
    followed = client.get(status.json()["status_url"])
    assert followed.status_code == 200
    assert "snapshot_dir" not in followed.json()
    assert str(tmp_path) not in followed.text
    assert artifact.status_code == 200
    assert artifact.json()["bytes_returned"] == 8
    assert "path" not in artifact.json()


class _RecordingWorkflowStarts:
    """Capture start-route kwargs without scheduling real retrieval work."""

    def __init__(self):
        self.runtime_values = []

    async def _start(self, **kwargs):
        self.runtime_values.append(kwargs.pop("runtime"))
        return WorkflowResult(
            run_id=f"run-{len(self.runtime_values)}",
            kind=WorkflowKind.BUILD_RESEARCH_PACK,
            status=WorkflowStatus.PENDING,
            target="target",
        )

    async def start_recover_article(self, **kwargs):
        return await self._start(**kwargs)

    async def start_capture_site(self, **kwargs):
        return await self._start(**kwargs)

    async def start_build_research_pack(self, **kwargs):
        return await self._start(**kwargs)

    async def start_search_and_summarize(self, **kwargs):
        return await self._start(**kwargs)


class _RuntimeStatus:
    def full_status(self):
        return {
            "build": {
                "version": "1.6.4",
                "source_revision": "a" * 40,
                "image_identity": "ghcr.io/khamel83/argus@sha256:" + "b" * 64,
                "source_path": "/srv/argus/source",
            },
            "deployment": {
                "deployment_id": "deploy-43",
                "database_url": "postgresql://user:secret@example/db",
            },
            "secret": "do-not-publish",
        }


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/workflows/recover-article",
            {"url": "https://dead.example/post"},
        ),
        (
            "/api/workflows/capture-site",
            {"url": "https://docs.example"},
        ),
        (
            "/api/workflows/build-research-pack",
            {"topic": "Argus"},
        ),
        (
            "/api/workflows/search-and-summarize",
            {"query": "Argus"},
        ),
    ],
)
def test_start_routes_capture_only_sanitized_runtime_identity(path, payload):
    workflows = _RecordingWorkflowStarts()
    app = FastAPI()
    app.state.get_workflows = lambda: workflows
    app.state.operational_status = _RuntimeStatus()
    from argus.api.routes_workflows import router

    app.include_router(router, prefix="/api")
    response = TestClient(app).post(path, json=payload)

    assert response.status_code == 200
    assert workflows.runtime_values == [
        {
            "version": "1.6.4",
            "source_revision": "a" * 40,
            "image_identity": "ghcr.io/khamel83/argus@sha256:" + "b" * 64,
            "deployment_identity": "deploy-43",
        }
    ]
    assert "do-not-publish" not in response.text
    assert "/srv/argus" not in response.text


@pytest.mark.asyncio
async def test_started_workflow_persists_runtime_identity_before_background_execution(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    from tests.test_workflows import _service

    service, _ = _service()
    entered = asyncio.Event()
    release = asyncio.Event()
    runtime = _RuntimeStatus().full_status()
    expected_runtime = {
        "version": "1.6.4",
        "source_revision": "a" * 40,
        "image_identity": "ghcr.io/khamel83/argus@sha256:" + "b" * 64,
        "deployment_identity": "deploy-43",
    }

    async def blocked_handler(run, **kwargs):
        del kwargs
        assert run.metadata["runtime"] == expected_runtime
        entered.set()
        await release.wait()
        run.summary_sections = []
        service._finalize_run(
            run,
            title="Runtime identity",
            report_name="report.md",
        )

    service._build_research_pack_impl = blocked_handler
    run = await service.start_build_research_pack(
        topic="Argus",
        runtime=runtime,
    )

    assert run.status is WorkflowStatus.PENDING
    assert run.metadata["runtime"] == expected_runtime
    await asyncio.wait_for(entered.wait(), timeout=1)
    release.set()

    async def wait_for_terminal():
        while run.status not in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_terminal(), timeout=1)
    assert run.status is WorkflowStatus.COMPLETED
    manifest = json.loads(Path(run.manifest_path).read_text(encoding="utf-8"))
    assert manifest["runtime"] == expected_runtime

    reloaded_service = WorkflowService(SimpleNamespace(), corpus_paths=service._paths)
    reloaded = reloaded_service.get_run(run.run_id)
    assert reloaded is not None
    assert reloaded_service.get_public_status(reloaded)["runtime"] == expected_runtime
    assert "/srv/argus" not in json.dumps(manifest)
    assert "do-not-publish" not in json.dumps(manifest)


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

    (tmp_path / "SUMMARY.md").write_text("😀 tail", encoding="utf-8")
    assert (
        client.get(
            f"/api/workflows/{run.run_id}/artifacts/report",
            params={"max_bytes": 1},
        ).status_code
        == 422
    )


class _MappingProxyOperations:
    """Live-shaped accepted operations for terminal-state serialization tests."""

    async def search(self, request, *, principal, request_id):
        del principal
        return AcceptedOperation(
            outcome=CanonicalOutcome.SUCCESS,
            request_id=request_id,
            result={
                "results": ({"url": request.query, "title": "Captured"},),
                "acceptance_receipt": {"receipt_ref": f"receipt:{request_id}"},
            },
            error=None,
        )

    async def acquire_site(self, request, *, principal, request_id):
        return await self.search(
            SimpleNamespace(query=request.url),
            principal=principal,
            request_id=request_id,
        )

    async def compose_workflow(
        self,
        retrieval,
        *,
        max_results,
        principal,
        request_id,
        **kwargs,
    ):
        del retrieval, max_results, principal, kwargs
        # AcceptedOperation freezes this nested result into mappingproxy values,
        # matching the live accepted-composition projection.
        result = {
            "composition_receipt_ref": "composition:mappingproxy",
            "accepted_artifact_refs": (),
            "degraded_artifact_refs": (),
            "rejected_extraction_refs": ("extract:mappingproxy",),
            "composition_trace": (MappingProxyType({"step": "artifact_floor_unmet"}),),
            "links": (
                MappingProxyType(
                    {
                        "result_cluster_ref": "cluster:mappingproxy",
                        "artifact_disposition": "diagnostic_only",
                        "metadata": MappingProxyType({"reason": "rejected"}),
                    }
                ),
            ),
            "artifacts": (),
        }
        return AcceptedOperation(
            outcome=CanonicalOutcome.EXTRACTION_FAILED,
            request_id=request_id,
            result=result,
            error=_operation_error(
                CanonicalOutcome.EXTRACTION_FAILED,
                request_id=request_id,
                detail="workflow composition failed",
            ),
        )


@pytest.mark.asyncio
async def test_failed_mappingproxy_composition_persists_terminal_state_and_legacy_get(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    operations = _MappingProxyOperations()
    service = WorkflowService(operations)

    result = await service.capture_site(
        url="https://mappingproxy.example/docs",
        soft_page_limit=1,
        hard_page_limit=1,
    )

    assert result.status is WorkflowStatus.FAILED
    from argus.workflows import service as workflow_service

    assert json.loads(
        json.dumps(
            {"failure": MappingProxyType({"nested": {"code": "rejected"}})},
            default=workflow_service._json_default,
        )
    ) == {"failure": {"nested": {"code": "rejected"}}}

    def legacy_response(workflows):
        app = FastAPI()
        app.state.get_workflows = lambda: workflows
        from argus.api.routes_workflows import router

        app.include_router(router, prefix="/api")
        return TestClient(app).get(f"/api/workflows/{result.run_id}")

    in_memory_response = legacy_response(service)
    assert in_memory_response.status_code == 200
    assert in_memory_response.json()["status"] == "failed"

    reloaded_service = WorkflowService(operations, corpus_paths=service._paths)
    reloaded = reloaded_service.get_run(result.run_id)
    assert reloaded is not None
    assert reloaded.status is WorkflowStatus.FAILED
    assert reloaded.metadata["composition"]["links"][0]["metadata"] == {
        "reason": "rejected"
    }

    response = legacy_response(reloaded_service)
    assert response.status_code == 200
    assert response.json()["status"] == "failed"


class _ResearchPackOperations:
    """Small accepted-operation double with diagnostic/usable projections."""

    def __init__(self, *, all_rejected=False, research_results=None):
        self.all_rejected = all_rejected
        self.acquire_calls = []
        self.search_calls = []
        self.compose_calls = []
        self.research_results = tuple(
            research_results
            if research_results is not None
            else (
                {"url": "https://blog.example.net/one", "title": "Blog One"},
                {"url": "https://blog.example.net/two", "title": "Blog Two"},
                {"url": "https://blog.example.net/three", "title": "Blog Three"},
                {"url": "https://notes.example.org/one", "title": "Notes One"},
                {"url": "https://notes.example.org/two", "title": "Notes Two"},
                {"url": "https://notes.example.org/three", "title": "Notes Three"},
                {"url": "https://research.other.com/one", "title": "Research One"},
            )
        )

    @staticmethod
    def _operation(request_id, results, *, scope):
        return AcceptedOperation(
            outcome=CanonicalOutcome.SUCCESS,
            request_id=request_id,
            result={
                "results": tuple(results),
                "acceptance_receipt": {"receipt_ref": f"receipt:{request_id}"},
                "scope": scope,
            },
            error=None,
        )

    async def search(self, request, *, principal, request_id):
        del principal
        self.search_calls.append(request)
        if request.mode == "research":
            return self._operation(request_id, self.research_results, scope="external")
        return self._operation(
            request_id,
            ({"url": request.query, "title": "Captured"},),
            scope="external",
        )

    async def acquire_site(self, request, *, principal, request_id):
        del principal
        self.acquire_calls.append(request)
        results = (
            {
                "url": "https://docs.example.com/diagnostic",
                "title": "Diagnostic Home",
            },
            {"url": "https://docs.example.com/guide", "title": "Guide"},
        )
        return self._operation(request_id, results, scope="official")

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
        del principal
        results = retrieval.result["results"]
        selected = (
            tuple(
                next(item for item in results if item["url"] == url)
                for url in selection_urls
            )
            if selection_urls is not None
            else tuple(results[:max_results])
        )
        scope = retrieval.result["scope"]
        self.compose_calls.append(
            {
                "scope": scope,
                "max_results": max_results,
                "selection_urls": selection_urls,
                **kwargs,
            }
        )
        if scope == "official":
            should_fail = self.all_rejected or kwargs.get("required_urls") is None
            dispositions = (
                ("diagnostic_only", "diagnostic_only")
                if should_fail
                else ("diagnostic_only", "partial")
            )
            outcome = (
                CanonicalOutcome.EXTRACTION_FAILED
                if should_fail
                else CanonicalOutcome.DEGRADED
            )
        else:
            dispositions = tuple("usable" for _ in selected)
            outcome = CanonicalOutcome.SUCCESS
        links = tuple(
            {
                "result_cluster_ref": f"cluster-{ordinal}",
                "artifact_disposition": dispositions[ordinal],
            }
            for ordinal in range(len(selected))
        )
        artifacts = tuple(
            {
                "url": item["url"],
                "title": item["title"],
                "text": "accepted content " * 40,
                "word_count": 80,
                "disposition": dispositions[ordinal],
                "extractor": "trafilatura",
            }
            for ordinal, item in enumerate(selected)
            if dispositions[ordinal] in {"usable", "partial"}
        )
        result = {
            "composition_receipt_ref": f"composition:{request_id}",
            "accepted_artifact_refs": tuple(
                f"artifact-{ordinal}"
                for ordinal, disposition in enumerate(dispositions)
                if disposition == "usable"
            ),
            "degraded_artifact_refs": tuple(
                f"artifact-{ordinal}"
                for ordinal, disposition in enumerate(dispositions)
                if disposition == "partial"
            ),
            "rejected_extraction_refs": tuple(
                f"extract-{ordinal}"
                for ordinal, disposition in enumerate(dispositions)
                if disposition == "diagnostic_only"
            ),
            "composition_trace": ("artifact_floor_met",),
            "links": links,
            "artifacts": artifacts,
        }
        return AcceptedOperation(
            outcome=outcome,
            request_id=request_id,
            result=result,
            error=(
                None
                if outcome in {CanonicalOutcome.SUCCESS, CanonicalOutcome.DEGRADED}
                else _operation_error(
                    outcome,
                    request_id=request_id,
                    detail="workflow composition failed",
                )
            ),
        )


def test_domain_root_uses_registrable_domains_and_safe_fallback():
    from argus.workflows import service as workflow_service

    assert workflow_service._domain_root("docs.foo.co.uk") == "foo.co.uk"
    assert workflow_service._domain_root("api.bar.co.uk") == "bar.co.uk"
    assert workflow_service._domain_root("www.weird.com") == "weird.com"
    assert workflow_service._domain_root("wwwweird.com") == "wwwweird.com"
    assert workflow_service._domain_root("localhost") == "localhost"


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", (40, 200))
async def test_research_url_planner_caps_search_request_and_selection(
    monkeypatch, tmp_path, limit
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    operations = _ResearchPackOperations()
    service = WorkflowService(operations)
    run = service._create_run(WorkflowKind.BUILD_RESEARCH_PACK, "Example SDK")

    _retrieval, urls = await service._discover_research_urls(
        "Example SDK",
        official_url="https://docs.example.com/guide",
        limit=limit,
        run=run,
    )

    request = next(item for item in operations.search_calls if item.mode == "research")
    assert request.max_results == 50
    assert len(urls) <= limit


@pytest.mark.asyncio
async def test_research_url_selection_excludes_official_registrable_domain(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    operations = _ResearchPackOperations(
        research_results=(
            {"url": "https://docs.foo.co.uk/official", "title": "Official"},
            {"url": "https://a.foo.co.uk/also-official", "title": "Official"},
            {"url": "https://bar.co.uk/one", "title": "Bar One"},
            {"url": "https://a.bar.co.uk/two", "title": "Bar Two"},
            {"url": "https://b.bar.co.uk/three", "title": "Bar Three"},
            {"url": "https://other.co.uk/one", "title": "Other One"},
        )
    )
    service = WorkflowService(operations)
    run = service._create_run(WorkflowKind.BUILD_RESEARCH_PACK, "Example SDK")

    _retrieval, urls = await service._discover_research_urls(
        "Example SDK",
        official_url="https://docs.foo.co.uk/guide",
        limit=3,
        run=run,
    )

    assert urls == [
        "https://bar.co.uk/one",
        "https://a.bar.co.uk/two",
        "https://other.co.uk/one",
    ]


class _NoopSummarizer:
    def __init__(self):
        self.calls = 0

    async def summarize(self, **kwargs):
        self.calls += 1
        return []


@pytest.mark.asyncio
async def test_research_pack_ignores_diagnostic_first_artifact_and_bounds_capture(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    operations = _ResearchPackOperations()
    summarizer = _NoopSummarizer()
    monkeypatch.setattr("argus.workflows.service.get_summarizer", lambda: summarizer)
    service = WorkflowService(operations)

    result = await service.build_research_pack(
        topic="Example SDK",
        official_url="https://docs.example.com/guide",
        max_research_pages=5,
    )

    assert result.status is WorkflowStatus.COMPLETED
    assert {document.url for document in result.documents} == {
        "https://docs.example.com/guide",
        "https://blog.example.net/one",
        "https://blog.example.net/two",
        "https://notes.example.org/one",
        "https://notes.example.org/two",
        "https://research.other.com/one",
    }
    assert all(
        __import__("pathlib").Path(document.artifact_path).exists()
        for document in result.documents
    )
    assert summarizer.calls == 1

    official_call = next(
        call for call in operations.compose_calls if call["scope"] == "official"
    )
    external_call = next(
        call for call in operations.compose_calls if call["scope"] == "external"
    )
    assert official_call["max_results"] <= 8
    assert len(official_call["selection_urls"]) <= 8
    assert official_call["required_urls"] == ("https://docs.example.com/guide",)
    assert official_call["minimum_artifacts"] == 1
    assert official_call["allow_partial"] is True
    assert external_call["max_results"] == 5
    assert len(external_call["selection_urls"]) == 5
    assert external_call["selection_urls"] == (
        "https://blog.example.net/one",
        "https://blog.example.net/two",
        "https://notes.example.org/one",
        "https://notes.example.org/two",
        "https://research.other.com/one",
    )
    assert external_call["required_urls"] == ()
    roots = [
        ".".join(urlparse(url).netloc.split(".")[-2:])
        for url in external_call["selection_urls"]
    ]
    assert all(roots.count(root) <= 2 for root in set(roots))
    assert operations.acquire_calls[0].soft_page_limit == 8
    assert operations.acquire_calls[0].hard_page_limit == 8


@pytest.mark.asyncio
async def test_research_pack_all_rejected_artifacts_fails_without_synthesis(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    operations = _ResearchPackOperations(all_rejected=True)
    summarizer = _NoopSummarizer()
    monkeypatch.setattr("argus.workflows.service.get_summarizer", lambda: summarizer)
    service = WorkflowService(operations)

    result = await service.build_research_pack(
        topic="Example SDK",
        official_url="https://docs.example.com/guide",
        max_research_pages=5,
    )

    assert result.status is WorkflowStatus.FAILED
    assert result.documents == []
    assert result.report_path is None
    assert result.manifest_path is None
    assert summarizer.calls == 0
    assert len(operations.compose_calls) == 1
    assert operations.compose_calls[0]["scope"] == "official"
    assert operations.compose_calls[0]["required_urls"] == ("https://docs.example.com/guide",)
    assert operations.compose_calls[0]["minimum_artifacts"] == 1
    assert operations.compose_calls[0]["allow_partial"] is True
