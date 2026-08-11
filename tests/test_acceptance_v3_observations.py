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
    validate_cache_trace({"status": "hit", "attempted": False, "eligible": True})
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
    first = {"status": 201, "duplicate": False, "capture_id": "capture"}
    replay = {"status": 200, "duplicate": True, "capture_id": "capture"}
    evidence = replay_capture_evidence(
        body=b'{"query":"canary"}', first_response=first, replay_response=replay
    )
    assert evidence["duplicate"] is True
    with pytest.raises(ObservationError, match="capture"):
        replay_capture_evidence(
            body=b"x",
            first_response=first,
            replay_response={"status": 200, "duplicate": True, "capture_id": "other"},
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
        def __init__(self):
            self.search = 0
            self.maya = 0
            self.start = 0

        def post_search(self, body):
            self.search += 1
            return {
                "status": 200,
                "cached": False,
                "traces": [{"provider": "github", "status": "empty"}],
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
                "caller": "argus",
                "pages": [],
            }

        def post_workflow_start(self, body):
            self.start += 1
            return {
                "status": 202,
                "run_id": "run-1",
                "kind": "build-research-pack",
                "target": "topic",
                "request_sha256": "a" * 64,
            }

    adapter = Adapter()
    result = runner.dispatch_canary(
        adapter,
        nonce="nonce-1234",
        marker_dir=tmp_path / "markers",
        workflow_body=json.dumps({"topic": "topic"}).encode(),
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
        )
    assert adapter.calls == 1
    assert (tmp_path / "markers" / "github-canary.json").is_file()
