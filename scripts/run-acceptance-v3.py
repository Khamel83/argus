#!/usr/bin/env python3
"""Run the bounded v3 acceptance procedure through injected adapters only.

The default CLI is deliberately hermetic: production adapters must be supplied
by a separately reviewed caller.  The runner never follows redirects, retries
an ambiguous POST, resolves credentials, or opens a provider/database socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Protocol

from argus.acceptance_v3.bundle import build_canary_fixture, write_bundle
from argus.acceptance_v3.contract import (
    bind_returned_run,
    canonical_hash,
    write_phase_marker,
)
from argus.acceptance_v3.observations import ObservationError, replay_capture_evidence


class AcceptanceAdapters(Protocol):
    """Injected transport surface; implementations live outside this harness."""

    def post_search(self, body: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def post_maya(self, body: bytes) -> Mapping[str, Any]: ...

    def post_workflow_start(self, body: bytes) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CanaryResult:
    fixture: Mapping[str, Any]
    search_response: Mapping[str, Any]
    maya_evidence: Mapping[str, Any]
    workflow_response: Mapping[str, Any]


def _aware_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _wire_bytes(value: Mapping[str, Any]) -> bytes:
    # The request hash in the execution contract binds these exact bytes, not
    # an adapter's envelope or headers.
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _wire_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_search_canary(response: Mapping[str, Any]) -> None:
    if response.get("status") != 200 or response.get("cached") is True:
        raise ObservationError("canary search must be one uncached HTTP 200")
    traces = response.get("traces")
    if not isinstance(traces, list) or len(traces) != 1:
        raise ObservationError("canary search must have exactly one provider trace")
    trace = traces[0]
    if trace.get("provider") != "github" or trace.get("status") not in {
        "success",
        "empty",
    }:
        raise ObservationError("canary search trace is not GitHub success/empty")
    if (
        response.get("paid_attempts", 0) != 0
        or response.get("non_github_batches", 0) != 0
    ):
        raise ObservationError("canary search included a forbidden provider")
    if response.get("legacy_delivery_intent"):
        raise ObservationError("canary search created legacy delivery intent")


def dispatch_canary(
    adapters: AcceptanceAdapters,
    *,
    nonce: str,
    marker_dir: Path,
    workflow_body: bytes,
    run_binding_path: Path | None = None,
) -> CanaryResult:
    """Dispatch the exact three canary POSTs and one start POST once each."""

    fixture = dict(build_canary_fixture(nonce))
    started = completed = _aware_now()
    maya = dict(fixture["maya_body"])
    maya["started_at"] = started
    maya["completed_at"] = completed
    fixture["maya_body"] = maya
    fixture["maya_body_sha256"] = canonical_hash(maya)
    maya_body = _wire_bytes(maya)
    marker_dir.mkdir(parents=True, exist_ok=True)

    # Markers are written before every POST.  If the call disconnects, the
    # marker remains and the caller must stop; this function never retries.
    write_phase_marker(
        marker_dir / "github-canary.json",
        phase="github-canary",
        identity={"body_sha256": canonical_hash(fixture["search_body"])},
    )
    search_response = adapters.post_search(fixture["search_body"])
    _require_search_canary(search_response)

    write_phase_marker(
        marker_dir / "maya-first.json",
        phase="maya-first",
        identity={"body_sha256": canonical_hash(maya)},
    )
    first = adapters.post_maya(maya_body)
    write_phase_marker(
        marker_dir / "maya-replay.json",
        phase="maya-replay",
        identity={"body_sha256": canonical_hash(maya)},
    )
    replay = adapters.post_maya(maya_body)
    maya_evidence = replay_capture_evidence(
        body=maya_body, first_response=first, replay_response=replay
    )

    write_phase_marker(
        marker_dir / "workflow-start.json",
        phase="workflow-start",
        identity={"body_sha256": _wire_hash(workflow_body)},
    )
    workflow_response = adapters.post_workflow_start(workflow_body)
    if workflow_response.get("status") not in {200, 202} or not isinstance(
        workflow_response.get("run_id"), str
    ):
        raise ObservationError("workflow start did not return a bounded run projection")
    if run_binding_path is not None:
        bind_returned_run(
            run_binding_path,
            run_id=workflow_response["run_id"],
            kind=str(workflow_response.get("kind", "build-research-pack")),
            topic=str(workflow_response.get("target", "")),
            request_sha256=str(workflow_response.get("request_sha256", "0" * 64)),
            body_sha256=_wire_hash(workflow_body),
            dispatched_at=started,
        )
    return CanaryResult(
        fixture=fixture,
        search_response=search_response,
        maya_evidence=maya_evidence,
        workflow_response=workflow_response,
    )


def _fixture_payload() -> dict[str, Any]:
    """Produce a safe, explicit not-run bundle for offline smoke checks."""

    from argus.acceptance_v3.bundle import EIGHT_GATES

    return {
        "manifest": {
            "schema": "argus-acceptance-v3/free-targeted",
            "status": "preflight_failed",
            "sections": {
                "artifact": "not_run",
                "claim_support": "not_run",
                "synthesis": "not_run",
                "scoring": "not_run",
            },
            "competitive_baseline": "not_applicable",
            "competitive_pair": "not_applicable",
        },
        "gates": {
            name: {
                "status": "FAIL",
                "reason": "hermetic fixture only",
                "evidence": ["status.json"],
            }
            for name in EIGHT_GATES
        },
        "score": {
            "status": "not_run",
            "reason": "hermetic fixture only",
            "cells": None,
        },
        "claim_support": {
            "status": "not_run",
            "reason": "hermetic fixture only",
            "requirements": None,
        },
        "recovery": {"status": "not_applicable", "reason": "no mutation"},
        "artifacts": {"status.json": b'{"status":"preflight_failed"}'},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--fixture", action="store_true", help="write a no-network preflight fixture"
    )
    args = parser.parse_args(argv)
    if not args.fixture:
        parser.error(
            "production adapters must be injected by a reviewed caller; use --fixture for offline output"
        )
    write_bundle(args.output, _fixture_payload())
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
