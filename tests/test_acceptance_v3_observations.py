"""Pure snapshot, spend, transport, and log evidence checks."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from argus.acceptance_v3.observations import (
    ObservationError,
    audit_spend_delta,
    build_snapshot,
    compare_predecessors,
    hash_opaque,
    replay_capture_evidence,
    scan_log_window,
    validate_cache_trace,
    validate_transport_pages,
)


def _runner_module():
    path = Path(__file__).parents[1] / "scripts" / "run-acceptance-v3.py"
    spec = importlib.util.spec_from_file_location("acceptance_v3_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_snapshot_is_db_utc_sorted_hashed_and_value_free():
    snapshot = build_snapshot(
        "spend",
        [
            {"id": "row-b", "observed_at": "2026-08-10T20:00:02Z", "amount": 0},
            {"id": "row-a", "observed_at": "2026-08-10T20:00:01Z", "amount": 0},
        ],
        observed_at=datetime(2026, 8, 10, 20, tzinfo=timezone.utc),
    )
    assert [row["id_sha256"] for row in snapshot["rows"]] == [
        hash_opaque("row-a"),
        hash_opaque("row-b"),
    ]
    assert snapshot["authority"] == "postgresql"
    assert snapshot["statement_timeout_ms"] == 15_000
    assert snapshot["lock_timeout_ms"] == 2_000
    assert "row-a" not in repr(snapshot)


def test_snapshot_rejects_sqlite_and_secrets():
    with pytest.raises(ObservationError, match="SQLite"):
        build_snapshot("x", [], authority="sqlite")
    with pytest.raises(ObservationError, match="sensitive"):
        build_snapshot("x", [{"authorization": "Bearer secret"}])


def test_snapshot_hashes_camel_case_opaque_identifiers_without_raw_values():
    snapshot = build_snapshot("x", [{"runId": "raw-run", "captureId": "raw-capture"}])
    row = snapshot["rows"][0]
    assert "raw-run" not in repr(snapshot)
    assert "raw-capture" not in repr(snapshot)
    assert row["run_id_sha256"] == hash_opaque("raw-run")
    assert row["capture_id_sha256"] == hash_opaque("raw-capture")


def test_spend_delta_rejects_every_forbidden_new_row_and_unledgered_billable_call():
    before = [{"id": "old", "observed_at": "2026-08-10T20:00:00Z"}]
    after = before + [
        {
            "id": "bad",
            "observed_at": "2026-08-10T20:00:01Z",
            "tier": 1,
            "reserved": 0.1,
            "actual": 0,
            "status": "uncertain",
            "caller": "wrong",
            "label": "wrong",
        }
    ]
    violations = audit_spend_delta(
        before,
        after,
        window_start="2026-08-10T19:59:00Z",
        window_end="2026-08-10T20:01:00Z",
        caller="mac-agents",
        label="tonight-acceptance-v3",
    )
    assert {item["code"] for item in violations} >= {
        "paid",
        "tier_above_zero",
        "reserved_nonzero",
        "uncertain",
        "caller_mismatch",
        "label_mismatch",
    }
    with pytest.raises(ObservationError, match="unledgered"):
        audit_spend_delta(
            before,
            before,
            window_start="2026-08-10T19:59:00Z",
            window_end="2026-08-10T20:01:00Z",
            caller="mac-agents",
            label="tonight-acceptance-v3",
            network_attempts=[{"provider": "brave", "tier": 1, "attempted": True}],
        )


def test_predecessor_immutable_fields_and_cache_hit_attempted_false():
    before = [{"id": "a", "immutable": "same", "count": 1}]
    after = [{"id": "a", "immutable": "changed", "count": 1}]
    with pytest.raises(ObservationError, match="immutable"):
        compare_predecessors(before, after)
    validate_cache_trace(
        {
            "status": "hit",
            "attempted": False,
            "eligible": True,
            "age_seconds": 0,
            "origin": "postgresql",
            "spend_provenance": {"ledgered": True},
        }
    )
    with pytest.raises(ObservationError, match="attempted"):
        validate_cache_trace({"status": "hit", "attempted": True, "eligible": True})


def test_transport_pages_require_contiguous_utf8_bounded_pages_and_terminal_hash():
    pages = [
        {"offset": 0, "data": '{"x":', "terminal": False},
        {"offset": 5, "data": " 1}", "terminal": True},
    ]
    result = validate_transport_pages(
        pages,
        expected_sha256=sha256("".join(p["data"] for p in pages).encode()).hexdigest(),
    )
    assert result["bytes"] == 8
    with pytest.raises(ObservationError, match="contiguous"):
        validate_transport_pages(
            [{"offset": 1, "data": "x", "terminal": True}],
            expected_sha256=sha256(b"x").hexdigest(),
        )


def test_maya_replay_is_byte_identical_and_one_duplicate():
    body = b'{"query":"canary"}'
    body_sha256 = sha256(body).hexdigest()
    capture_sha256 = sha256(b"capture").hexdigest()
    first = {
        "status": 201,
        "duplicate": False,
        "capture_id": "capture",
        "capture_id_sha256": capture_sha256,
        "caller": "argus",
        "pages": [],
        "body_sha256": body_sha256,
    }
    replay = {
        "status": 200,
        "duplicate": True,
        "capture_id": "capture",
        "capture_id_sha256": capture_sha256,
        "caller": "argus",
        "pages": [],
        "body_sha256": body_sha256,
    }
    evidence = replay_capture_evidence(
        body=body, first_response=first, replay_response=replay
    )
    assert evidence["duplicate"] is True
    with pytest.raises(ObservationError, match="capture"):
        replay_capture_evidence(
            body=body,
            first_response=first,
            replay_response={
                "status": 200,
                "duplicate": True,
                "capture_id": "other",
                "capture_id_sha256": sha256(b"other").hexdigest(),
                "caller": "argus",
                "pages": [],
                "body_sha256": body_sha256,
            },
        )


def test_log_window_is_bounded_redacted_and_rejects_unexpected_errors():
    logs = [
        {"at": "2026-08-10T20:00:00Z", "status": 401, "request_hash": "a" * 64},
        {"at": "2026-08-10T20:00:01Z", "status": 200, "message": "ok"},
    ]
    result = scan_log_window(logs, allowed_request_hashes={"a" * 64})
    assert result["unexpected"] == []
    assert result["sha256"]
    with pytest.raises(ObservationError, match="unexpected"):
        scan_log_window([{"at": "now", "status": 500}], allowed_request_hashes=set())


def test_injected_canary_dispatches_exactly_three_canary_posts_and_one_start(tmp_path):
    runner = _runner_module()

    class Adapter:
        maya_calls = 0

        def __init__(self):
            self.search = 0
            self.maya = 0
            self.start = 0

        def post_search(self, body):
            self.search += 1
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
            self.maya += 1
            return {
                "status": 201 if self.maya == 1 else 200,
                "duplicate": self.maya != 1,
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
            self.start += 1
            return {
                "status": 202,
                "run_id": "run-1",
                "kind": "build-research-pack",
                "target": "topic",
                "request_sha256": runner.canonical_hash(json.loads(body)),
                "body_sha256": sha256(body).hexdigest(),
            }

    adapter = Adapter()
    result = runner.dispatch_canary(
        adapter,
        nonce="nonce-1234",
        marker_dir=tmp_path / "markers",
        workflow_body=json.dumps({"topic": "topic"}).encode(),
        run_binding_path=tmp_path / "run.json",
    )
    assert result.fixture["search_body"]["providers"] == ["github"]
    assert (adapter.search, adapter.maya, adapter.start) == (1, 2, 1)
    assert sorted(path.name for path in (tmp_path / "markers").iterdir()) == [
        "github-canary.json",
        "maya-first.json",
        "maya-replay.json",
        "workflow-start.json",
    ]


def test_injected_timeout_is_consumed_once_and_never_retried(tmp_path):
    runner = _runner_module()

    class Timeout:
        calls = 0

        def post_search(self, body):
            self.calls += 1
            raise TimeoutError("ambiguous disconnect")

    adapter = Timeout()
    with pytest.raises(TimeoutError):
        runner.dispatch_canary(
            adapter,
            nonce="nonce-1234",
            marker_dir=tmp_path / "markers",
            workflow_body=b"{}",
            run_binding_path=tmp_path / "run.json",
        )
    assert adapter.calls == 1
    assert (tmp_path / "markers" / "github-canary.json").is_file()


def test_canary_requires_explicit_workflow_identity_before_binding(tmp_path):
    runner = _runner_module()

    class Adapter:
        def __init__(self):
            self.maya_calls = 0

        def post_search(self, body):
            return {
                "status": 200,
                "cached": False,
                "traces": [{"provider": "github", "status": "empty"}],
                "paid_attempts": 0,
                "non_github_batches": 0,
                "legacy_delivery_intent": False,
            }

        def post_maya(self, body):
            self.maya_calls += 1
            return {
                "status": 201 if self.maya_calls == 1 else 200,
                "duplicate": self.maya_calls != 1,
                "capture_id": "capture-1",
                "caller": "argus",
                "pages": [],
            }

        def post_workflow_start(self, body):
            return {"status": 202, "run_id": "run-1"}

    with pytest.raises(ObservationError, match="identity"):
        _ = runner.dispatch_canary(
            Adapter(),
            nonce="nonce-1234",
            marker_dir=tmp_path / "markers",
            workflow_body=b"{}",
            run_binding_path=tmp_path / "run.json",
        )


def test_execute_cycle_fails_closed_when_no_injected_preflight(tmp_path):
    runner = _runner_module()

    result = runner.execute_cycle(
        object(),
        output=tmp_path / "bundle",
        nonce="nonce-1234",
        marker_dir=tmp_path / "markers",
        workflow_body=b"{}",
        run_binding_path=tmp_path / "run.json",
    )
    assert result["status"] == "preflight_failed"
    assert result["verdict"] == "FAIL"


def test_execute_cycle_rejects_non_global_guard_before_any_adapter_post(tmp_path):
    runner = _runner_module()

    class BadGuard:
        def __init__(self):
            self.posts = 0

        def preflight(self):
            return {
                "status": "ready",
                "execution_contract": {"guard_path": str(tmp_path / "guard.json")},
                "execution_contract_path": str(tmp_path / "contract.json"),
                "guard_path": str(tmp_path / "guard.json"),
            }

        def post_search(self, body):
            self.posts += 1
            raise AssertionError("guard rejection must precede POST")

    adapter = BadGuard()
    result = runner.execute_cycle(
        adapter,
        output=tmp_path / "bundle",
        nonce="nonce-1234",
        marker_dir=tmp_path / "markers",
        workflow_body=b"{}",
        run_binding_path=tmp_path / "run.json",
    )
    assert result["status"] == "preflight_failed"
    assert adapter.posts == 0
