"""Regression tests for the final v3 artifact and recovery semantics."""

from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from argus.acceptance_v3.observations import ObservationError, validate_transport_pages
from argus.acceptance_v3.bundle import build_canary_fixture


def _runner_module():
    path = Path(__file__).parents[1] / "scripts" / "run-acceptance-v3.py"
    spec = importlib.util.spec_from_file_location("acceptance_v3_followup_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_artifact_pages_are_bounded_to_mcp_http_page_size():
    data = "x" * 65_537
    with pytest.raises(ObservationError, match="page"):
        validate_transport_pages(
            [{"offset": 0, "data": data, "terminal": True}],
            expected_sha256=sha256(data.encode()).hexdigest(),
        )


def test_evaluator_failure_retains_both_completed_workflow_artifacts():
    runner = _runner_module()
    report = b'{"status":"completed","report":true}'
    manifest = b'{"status":"completed","manifest":true}'
    payload = runner._failure_payload(
        "evaluator_not_run",
        "evaluator unavailable",
        artifacts={"report.json": report, "manifest.json": manifest},
    )
    assert payload["artifacts"] == {
        "report.json": report,
        "manifest.json": manifest,
        "status.json": payload["artifacts"]["status.json"],
    }


def test_preflight_observation_valid_flag_without_contents_is_rejected():
    runner = _runner_module()
    observations = {
        name: {"valid": True}
        for name in {
            "spend",
            "balance",
            "outbox",
            "policy",
            "logs",
            "unauth_probe",
            "audit",
            "network",
        }
    }
    assert runner._preflight_observations_ready({"observations": observations}) is False


def test_workflow_artifact_reader_requires_kind_and_transport_arguments():
    runner = _runner_module()

    class LegacyReader:
        def read_artifact(self, run_id, transport):
            return {"run_id": run_id, "transport": transport}

    assert runner._artifact_reader_signature_valid(LegacyReader()) is False


def test_strict_canary_binds_contract_hashes_and_safe_pending_start(
    tmp_path, monkeypatch
):
    runner = _runner_module()
    timestamp = "2026-08-10T20:00:00Z"
    monkeypatch.setattr(runner, "_aware_now", lambda: "2026-08-10T20:00:01Z")
    fixture = build_canary_fixture(
        "nonce-1234", started_at=timestamp, completed_at=timestamp
    )

    class Adapter:
        maya_calls = 0

        def __init__(self):
            self.maya_bodies = []

        def post_search(self, body):
            return {
                "status": 200,
                "cached": False,
                "caller": "mac-agents",
                "body_sha256": runner.canonical_hash(body),
                "traces": [{"provider": "github", "status": "empty"}],
                "accepted_operations": 1,
                "plan_batches": 1,
                "batches": 1,
                "paid_attempts": 0,
                "non_github_batches": 0,
                "legacy_delivery_intent": False,
            }

        def post_maya(self, body):
            self.maya_calls += 1
            self.maya_bodies.append(body)
            return {
                "status": 201 if self.maya_calls == 1 else 200,
                "duplicate": self.maya_calls != 1,
                "capture_id": "capture-1",
                "capture_id_sha256": sha256(b"capture-1").hexdigest(),
                "idempotency_key_sha256": sha256(
                    json.loads(body)["idempotency_key"].encode()
                ).hexdigest(),
                "caller": "argus",
                "pages": [],
                "body_sha256": sha256(body).hexdigest(),
            }

        def post_workflow_start(self, body):
            return {
                "http_status": 202,
                "status": "pending",
                "run_id": "run-1",
                "kind": "build-research-pack",
                "target": "topic",
                "created_at": timestamp,
                "status_url": "/api/workflows/run-1/status",
                "request_sha256": runner.canonical_hash(json.loads(body)),
                "body_sha256": sha256(body).hexdigest(),
            }

    adapter = Adapter()
    result = runner.dispatch_canary(
        adapter,
        nonce="nonce-1234",
        marker_dir=tmp_path / "markers",
        workflow_body=b"{}",
        run_binding_path=tmp_path / "run.json",
        expected_canary={
            "query_sha256": runner.canonical_hash(fixture["query"]),
            "search_body_sha256": fixture["search_body_sha256"],
            "maya_body_sha256": fixture["maya_body_sha256"],
            "idempotency_key_sha256": fixture["idempotency_key_sha256"],
        },
        canary_timestamp=timestamp,
        require_safe_start=True,
    )
    assert result.workflow_response["status"] == "pending"
    expected_maya = runner._wire_bytes(fixture["maya_body"])
    assert adapter.maya_bodies == [expected_maya, expected_maya]


def test_rollback_receipt_requires_baseline_and_quiescence_proof():
    runner = _runner_module()
    complete = {
        "status": "complete",
        "proof": "restored",
        "proof_sha256": runner.canonical_hash("restored"),
        "backup_sha256": "a" * 64,
        "restore_sha256": "b" * 64,
        "schema_sha256": "c" * 64,
        "identity_sha256": "d" * 64,
        "soak_sha256": "e" * 64,
        "before_sha256": "f" * 64,
        "after_sha256": "0" * 64,
    }
    complete_ok, _ = runner._rollback_recovery(complete, "failed")
    assert complete_ok is False
