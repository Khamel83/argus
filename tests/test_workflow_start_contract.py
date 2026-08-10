"""Acceptance-v3 durable safe workflow-start contract."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from argus.api.routes_workflows import router
from argus.api.schemas import BuildResearchPackWorkflowRequest
from argus.workflows import WorkflowService
from argus.workflows.models import WorkflowKind, WorkflowResult, WorkflowStatus
from argus.workflows.research_targets import canonical_request_sha256


def _target_request() -> BuildResearchPackWorkflowRequest:
    return BuildResearchPackWorkflowRequest.model_validate(
        {
            "topic": "Managed web research",
            "official_url": None,
            "max_research_pages": 3,
            "free_only": True,
            "caller": "body-label",
            "research_targets": [
                {
                    "name": "Example",
                    "source_prefixes": ["https://example.com/docs/"],
                    "requirements": [
                        {"claim_class": "capabilities", "query": "capabilities"}
                    ],
                }
            ],
        }
    )


def _route_app(service: WorkflowService) -> FastAPI:
    app = FastAPI()
    app.state.get_workflows = lambda: service
    app.state.operational_status = SimpleNamespace(full_status=lambda: {})
    app.include_router(router, prefix="/api")
    return app


@pytest.mark.asyncio
async def test_safe_start_persists_pending_before_scheduling_and_returns_exact_safe_keys(
    tmp_path: Path,
):
    service = WorkflowService(SimpleNamespace(), corpus_paths=_paths(tmp_path))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_handler(run, **kwargs):
        del kwargs
        entered.set()
        await release.wait()

    service._build_research_pack_impl = blocked_handler
    request = _target_request()
    runtime = {
        "build": {"version": "1.6.4", "source_revision": "a" * 40},
        "deployment": {"deployment_id": "deploy-start"},
    }

    run = await service.start_build_research_pack_safe(
        request=request,
        caller_identity="mac-agents",
        caller_label=request.caller,
        runtime=runtime,
    )
    assert run.status is WorkflowStatus.PENDING
    payload = service.safe_start_response(run)
    assert set(payload) == {
        "run_id",
        "kind",
        "status",
        "target",
        "created_at",
        "status_url",
        "request_sha256",
    }
    assert payload["kind"] == "build-research-pack"
    assert payload["status"] == "pending"
    assert payload["status_url"].endswith(f"/{run.run_id}/status")
    assert datetime.fromisoformat(payload["created_at"]).tzinfo is not None
    assert payload["request_sha256"] == canonical_request_sha256(request)

    state = json.loads(
        (service._paths.workflow_runs_dir / f"{run.run_id}.json").read_text()
    )
    assert state["status"] == "pending"
    assert state["metadata"]["request_sha256"] == payload["request_sha256"]
    assert state["metadata"]["caller_identity"] == "mac-agents"
    assert state["metadata"]["caller_label"] == "body-label"
    assert state["metadata"]["free_only"] is True
    assert state["metadata"]["research_plan"]["targets"][0]["name"] == "Example"
    assert state["metadata"]["runtime"] == {
        "version": "1.6.4",
        "source_revision": "a" * 40,
        "image_identity": "unknown",
        "deployment_identity": "deploy-start",
    }
    deadline = datetime.fromisoformat(state["metadata"]["deadline_at"])
    assert deadline.tzinfo is not None
    assert deadline > run.created_at
    status = service.get_public_status(run)
    assert status["research_plan"]["targets"][0]["source_prefixes"] == [
        "https://example.com/docs/"
    ]

    await asyncio.wait_for(entered.wait(), timeout=1)
    release.set()


def test_safe_http_response_has_no_legacy_paths_or_payloads(tmp_path: Path):
    service = WorkflowService(SimpleNamespace(), corpus_paths=_paths(tmp_path))
    app = _route_app(service)
    client = TestClient(app)

    response = client.post(
        "/api/workflows/build-research-pack/start",
        json={"topic": "Safe start", "caller": "label"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert set(payload) == {
        "run_id",
        "kind",
        "status",
        "target",
        "created_at",
        "status_url",
        "request_sha256",
    }
    encoded = json.dumps(payload).lower()
    assert not any(
        marker in encoded
        for marker in (
            "snapshot",
            "report_path",
            "manifest_path",
            "receipt",
            "exception",
            "provider",
            "document",
        )
    )


@pytest.mark.asyncio
async def test_initial_persistence_failure_is_terminal_and_reloadable(
    monkeypatch, tmp_path: Path
):
    service = WorkflowService(SimpleNamespace(), corpus_paths=_paths(tmp_path))
    original = service._write_run_state
    attempts = 0

    def fail_once(run):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("injected persistence failure")
        return original(run)

    monkeypatch.setattr(service, "_write_run_state", fail_once)
    run = await service.start_build_research_pack_safe(
        request=BuildResearchPackWorkflowRequest(topic="persist me"),
        caller_identity="principal",
    )

    assert run.status is WorkflowStatus.FAILED
    assert run.error == "WorkflowStatePersistenceError"
    assert not service._tasks
    loaded = WorkflowService(SimpleNamespace(), corpus_paths=service._paths).get_run(
        run.run_id
    )
    assert loaded is not None
    assert loaded.status is WorkflowStatus.FAILED
    assert loaded.error == "WorkflowStatePersistenceError"


@pytest.mark.asyncio
async def test_running_persistence_failure_is_terminal_without_orphan_or_publication(
    monkeypatch, tmp_path: Path
):
    service = WorkflowService(SimpleNamespace(), corpus_paths=_paths(tmp_path))
    original = service._write_run_state
    attempts = 0

    def fail_running_transition(run):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise OSError("injected running transition persistence failure")
        return original(run)

    monkeypatch.setattr(service, "_write_run_state", fail_running_transition)
    handler_called = False

    async def handler(run, **kwargs):
        del run, kwargs
        nonlocal handler_called
        handler_called = True

    service._build_research_pack_impl = handler
    run = await service.start_build_research_pack_safe(
        request=BuildResearchPackWorkflowRequest(topic="running persistence")
    )
    task = service._tasks[run.run_id]

    for _ in range(10):
        await asyncio.sleep(0)
        if task.done() and run.run_id not in service._tasks:
            break
    if task.done():
        # Retrieve any pre-fix task exception so the regression asserts the
        # durable/public state rather than relying on an event-loop warning.
        task.exception()

    assert attempts >= 3
    assert not handler_called
    assert not service._tasks
    assert run.status is WorkflowStatus.FAILED
    assert run.error == "WorkflowStatePersistenceError"
    assert run.metadata["failure"] == {
        "code": "WorkflowStatePersistenceError",
        "reason": "running_state_write_failed",
    }

    state = json.loads(
        (service._paths.workflow_runs_dir / f"{run.run_id}.json").read_text()
    )
    assert state["status"] == "failed"
    assert state["error"] == "WorkflowStatePersistenceError"
    snapshot = Path(run.snapshot_dir)
    assert not (snapshot / "SUMMARY.md").exists()
    assert not (snapshot / "manifest.json").exists()

    status = service.get_public_status(run)
    encoded = json.dumps(status).lower()
    assert status["error_code"] == "WorkflowStatePersistenceError"
    assert all(not artifact["available"] for artifact in status["artifacts"])
    assert "injected running transition persistence failure" not in encoded
    assert "exception" not in encoded
    assert "provider" not in encoded


@pytest.mark.asyncio
async def test_reloaded_targeted_pending_run_is_interrupted_without_operations(
    tmp_path: Path,
):
    service = WorkflowService(SimpleNamespace(), corpus_paths=_paths(tmp_path))
    service._schedule_run = lambda *args, **kwargs: None
    request = _target_request()
    run = await service.start_build_research_pack_safe(
        request=request,
        caller_identity="mac-agents",
        caller_label=request.caller,
    )
    assert run.status is WorkflowStatus.PENDING
    state_path = service._paths.workflow_runs_dir / f"{run.run_id}.json"
    assert not Path(run.snapshot_dir, "SUMMARY.md").exists()

    reloaded = WorkflowService(SimpleNamespace(), corpus_paths=service._paths)
    interrupted = reloaded.get_run(run.run_id)

    assert interrupted is not None
    assert interrupted.status is WorkflowStatus.FAILED
    assert interrupted.error == "workflow_interrupted"
    assert interrupted.metadata["request_sha256"] == run.metadata["request_sha256"]
    assert interrupted.metadata["deadline_at"] == run.metadata["deadline_at"]
    assert interrupted.metadata["runtime"] == run.metadata["runtime"]
    persisted = json.loads(state_path.read_text())
    assert persisted["status"] == "failed"
    assert persisted["error"] == "workflow_interrupted"
    assert not Path(run.snapshot_dir, "SUMMARY.md").exists()
    assert not Path(run.snapshot_dir, "manifest.json").exists()


def test_terminal_status_prefers_persisted_runtime_and_reports_live_mismatch(tmp_path):
    service = WorkflowService(SimpleNamespace(), corpus_paths=_paths(tmp_path))
    run = WorkflowResult(
        run_id="runtime-run",
        kind=WorkflowKind.BUILD_RESEARCH_PACK,
        status=WorkflowStatus.COMPLETED,
        target="topic",
        metadata={
            "runtime": {
                "version": "1.6.4",
                "source_revision": "a" * 40,
                "image_identity": "unknown",
                "deployment_identity": "deploy-old",
            }
        },
    )

    status = service.get_public_status(
        run,
        runtime={
            "build": {"version": "1.6.5", "source_revision": "b" * 40},
            "deployment": {"deployment_id": "deploy-new"},
        },
    )

    assert status["runtime"] == run.metadata["runtime"]
    assert status["runtime_observation"]["mismatch"] is True
    assert status["runtime_observation"]["persisted"] == run.metadata["runtime"]
    assert status["runtime_observation"]["live"]["deployment_identity"] == "deploy-new"


def _paths(tmp_path: Path):
    from argus.corpus.paths import CorpusPaths

    data = tmp_path / "data"
    paths = CorpusPaths(
        data_root=data,
        docs_root=data / "docs",
        docs_cache_dir=data / "docs" / "cache",
        docs_cache_index=data / "docs" / "cache" / ".index.md",
        research_dir=data / "docs" / "research",
        workflow_runs_dir=data / "workflows" / "runs",
        snapshots_dir=data / "snapshots",
        imports_dir=data / "imports",
    )
    return paths.ensure()
