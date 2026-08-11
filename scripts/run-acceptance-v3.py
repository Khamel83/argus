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
import importlib
import json
from pathlib import Path
from typing import Any, Mapping, Protocol

from argus.acceptance_v3.bundle import (
    build_canary_fixture,
    verify_bundle,
    write_bundle,
)
from argus.acceptance_v3.contract import (
    ContractError,
    bind_returned_run,
    canonical_hash,
    create_global_guard,
    validate_execution_contract,
    write_execution_contract,
    write_phase_marker,
)
from argus.acceptance_v3.observations import ObservationError, replay_capture_evidence


class AcceptanceAdapters(Protocol):
    """Injected transport surface; implementations live outside this harness."""

    def post_search(self, body: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def post_maya(self, body: bytes) -> Mapping[str, Any]: ...

    def post_workflow_start(self, body: bytes) -> Mapping[str, Any]: ...


class CycleAdapters(AcceptanceAdapters, Protocol):
    """Complete cycle seam; every method is supplied by the reviewed caller."""

    def preflight(self) -> Mapping[str, Any]: ...

    def snapshot(self, phase: str) -> Mapping[str, Any]: ...

    def poll_status(self, run_id: str) -> Mapping[str, Any]: ...

    def read_artifact(self, run_id: str, transport: str) -> bytes: ...

    def evaluate(self, artifact: bytes) -> Mapping[str, Any]: ...

    def rollback(self, reason: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CanaryResult:
    fixture: Mapping[str, Any]
    search_response: Mapping[str, Any]
    maya_evidence: Mapping[str, Any]
    workflow_response: Mapping[str, Any]


def _failure_payload(
    status: str, reason: str, *, artifact: bytes | None = None
) -> dict[str, Any]:
    """Create a machine-readable non-fabricating branch payload."""

    from argus.acceptance_v3.bundle import EIGHT_GATES

    if status == "evaluator_not_run":
        sections = {
            "artifact": "report.json",
            "claim_support": "not_run",
            "synthesis": "not_run",
            "scoring": "not_run",
        }
    else:
        sections = {
            "artifact": "not_run",
            "claim_support": "not_run",
            "synthesis": "not_run",
            "scoring": "not_run",
        }
    artifacts: dict[str, bytes] = {
        "status.json": json.dumps({"status": status}).encode()
    }
    if status == "evaluator_not_run":
        artifacts["report.json"] = artifact or b'{"status":"artifact_complete"}'
    return {
        "manifest": {
            "schema": "argus-acceptance-v3/free-targeted",
            "status": status,
            "sections": sections,
            "competitive_baseline": "not_applicable",
            "competitive_pair": "not_applicable",
        },
        "gates": {
            name: {"status": "FAIL", "reason": reason, "evidence": ["status.json"]}
            for name in EIGHT_GATES
        },
        "score": {"status": "not_run", "reason": reason, "cells": None},
        "claim_support": {
            "status": "not_run",
            "reason": reason,
            "requirements": None,
        },
        "recovery": {
            "status": "not_applicable",
            "reason": "no mutation",
            "proof": "no mutation",
            "proof_sha256": "0" * 64,
        },
        "artifacts": artifacts,
    }


def execute_cycle(
    adapters: CycleAdapters,
    *,
    output: Path,
    nonce: str,
    marker_dir: Path,
    workflow_body: bytes,
    run_binding_path: Path,
) -> Mapping[str, Any]:
    """Run one fail-closed cycle using only injected, already-authorized methods.

    Every adapter call is intentionally single-shot.  A timeout, malformed
    projection, missing capability, or rollback error writes a terminal branch
    and returns; the harness never retries an ambiguous side effect.
    """

    def publish(status: str, reason: str, *, artifact: bytes | None = None):
        write_bundle(output, _failure_payload(status, reason, artifact=artifact))
        return verify_bundle(output)

    side_effects_started = False
    try:
        preflight = adapters.preflight()
    except Exception as exc:
        return publish(
            "preflight_failed", f"preflight adapter failed: {type(exc).__name__}"
        )
    if not isinstance(preflight, Mapping) or preflight.get("status") != "ready":
        return publish(
            "preflight_failed", "preflight did not establish ready authority"
        )
    execution_contract = preflight.get("execution_contract")
    contract_path = preflight.get("execution_contract_path")
    guard_path = preflight.get("guard_path")
    if (
        not isinstance(execution_contract, Mapping)
        or not isinstance(contract_path, str)
        or not isinstance(guard_path, str)
    ):
        return publish(
            "preflight_failed",
            "preflight did not provide immutable execution contract and guard",
        )
    try:
        prepare_guard = getattr(adapters, "prepare_guard", None)
        if callable(prepare_guard):
            prepared = prepare_guard(
                execution_contract,
                contract_path=Path(contract_path),
                guard_path=Path(guard_path),
            )
            if not isinstance(prepared, Mapping):
                raise ContractError("injected guard preparation returned no receipt")
        else:
            write_execution_contract(Path(contract_path), execution_contract)
            guard = create_global_guard(Path(guard_path), execution_contract)
            prepared = {
                "created": True,
                "o_excl": True,
                "contract_path": str(contract_path),
                "guard_path": str(guard_path),
                "execution_contract_sha256": guard["execution_contract_sha256"],
            }
        _verify_prepared_guard(
            prepared,
            execution_contract,
            contract_path=Path(contract_path),
            guard_path=Path(guard_path),
        )
    except (ContractError, OSError, ValueError) as exc:
        return publish(
            "preflight_failed",
            f"contract/guard preparation failed: {type(exc).__name__}",
        )
    for name in ("snapshot", "poll_status", "read_artifact", "evaluate", "rollback"):
        if not callable(getattr(adapters, name, None)):
            return publish("preflight_failed", f"required adapter missing: {name}")
    try:
        pre_snapshot = adapters.snapshot("pre_canary")
        if not isinstance(pre_snapshot, Mapping):
            return publish("preflight_failed", "pre-canary snapshot is not an object")
        side_effects_started = True
        canary = dispatch_canary(
            adapters,
            nonce=nonce,
            marker_dir=marker_dir,
            workflow_body=workflow_body,
            run_binding_path=run_binding_path,
        )
        post_canary_snapshot = adapters.snapshot("post_canary")
        if not isinstance(post_canary_snapshot, Mapping):
            raise ValueError("post-canary snapshot is not an object")
        run_id = str(canary.workflow_response["run_id"])
        terminal = adapters.poll_status(run_id)
        if not isinstance(terminal, Mapping) or terminal.get("status") not in {
            "completed",
            "failed",
        }:
            reason = "workflow did not reach completed status"
            try:
                rollback = adapters.rollback(reason)
            except Exception:
                return publish("rollback_incomplete", "rollback adapter failed")
            if (
                not isinstance(rollback, Mapping)
                or rollback.get("status") != "complete"
            ):
                return publish("rollback_incomplete", "rollback did not complete")
            return publish("FAIL", reason)
        http_artifact = adapters.read_artifact(run_id, "http")
        mcp_artifact = adapters.read_artifact(run_id, "mcp")
        if (
            not isinstance(http_artifact, bytes)
            or not isinstance(mcp_artifact, bytes)
            or not http_artifact
            or not mcp_artifact
            or http_artifact != mcp_artifact
        ):
            raise ValueError(
                "artifact transport did not return byte-identical evidence"
            )
        post_benchmark_snapshot = adapters.snapshot("post_benchmark")
        if not isinstance(post_benchmark_snapshot, Mapping):
            raise ValueError("post-benchmark snapshot is not an object")
        evaluation = adapters.evaluate(http_artifact)
        if not isinstance(evaluation, Mapping) or evaluation.get("status") != "scored":
            return publish(
                "evaluator_not_run",
                "evaluator did not return a scored identity",
                artifact=http_artifact,
            )
        payload = evaluation.get("bundle_payload")
        if not isinstance(payload, Mapping):
            return publish(
                "evaluator_not_run",
                "evaluator returned no bound bundle payload",
                artifact=http_artifact,
            )
        write_bundle(output, payload)
        return verify_bundle(output)
    except (ObservationError, ContractError, ValueError) as exc:
        if side_effects_started:
            try:
                rollback = adapters.rollback(
                    f"cycle contract rejected: {type(exc).__name__}"
                )
            except Exception:
                return publish("rollback_incomplete", "rollback adapter failed")
            if (
                not isinstance(rollback, Mapping)
                or rollback.get("status") != "complete"
            ):
                return publish("rollback_incomplete", "rollback did not complete")
            return publish("FAIL", f"cycle contract rejected: {type(exc).__name__}")
        return publish(
            "preflight_failed", f"cycle contract rejected: {type(exc).__name__}"
        )
    except Exception as exc:
        try:
            rollback = adapters.rollback(f"cycle failed: {type(exc).__name__}")
        except Exception:
            return publish("rollback_incomplete", "rollback adapter failed")
        if not isinstance(rollback, Mapping) or rollback.get("status") != "complete":
            return publish("rollback_incomplete", "rollback did not complete")
        return publish("FAIL", f"cycle failed: {type(exc).__name__}")


run_acceptance_v3 = execute_cycle


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


def _verify_prepared_guard(
    prepared: Mapping[str, Any],
    execution_contract: Mapping[str, Any],
    *,
    contract_path: Path,
    guard_path: Path,
) -> None:
    """Verify a guard receipt by rereading the exact fsynced files."""

    if prepared.get("created") is not True or prepared.get("o_excl") is not True:
        raise ContractError("guard receipt does not prove immutable O_EXCL creation")
    if prepared.get("contract_path") != str(contract_path) or prepared.get(
        "guard_path"
    ) != str(guard_path):
        raise ContractError("guard receipt paths do not match the contract")
    if contract_path.is_symlink() or guard_path.is_symlink():
        raise ContractError("guard files must not be symlinks")
    if not contract_path.is_file() or not guard_path.is_file():
        raise ContractError("guard files are missing")
    if (
        contract_path.stat().st_mode & 0o7777 != 0o600
        or guard_path.stat().st_mode & 0o7777 != 0o600
    ):
        raise ContractError("guard files must have mode 0600")
    try:
        persisted = json.loads(contract_path.read_text(encoding="utf-8"))
        guard = json.loads(guard_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("guard files are not canonical JSON") from exc
    checked = validate_execution_contract(persisted)
    if canonical_hash(checked) != canonical_hash(execution_contract):
        raise ContractError("persisted execution contract hash mismatch")
    contract_hash = canonical_hash(checked)
    if prepared.get("execution_contract_sha256") != contract_hash:
        raise ContractError("guard receipt contract hash mismatch")
    if (
        not isinstance(guard, Mapping)
        or guard.get("execution_contract_sha256") != contract_hash
    ):
        raise ContractError("global guard is not bound to the execution contract")
    if (
        guard.get("cycle_id") != checked["cycle_id"]
        or guard.get("schema") != checked["schema"]
    ):
        raise ContractError("global guard identity mismatch")


def _require_search_canary(response: Mapping[str, Any]) -> None:
    required = {
        "status",
        "cached",
        "traces",
        "accepted_operations",
        "plan_batches",
        "batches",
        "paid_attempts",
        "non_github_batches",
        "legacy_delivery_intent",
    }
    if not required.issubset(response):
        raise ObservationError("canary search response identity is incomplete")
    if response.get("status") != 200 or response.get("cached") is not False:
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
        response.get("accepted_operations") != 1
        or response.get("plan_batches") != 1
        or response.get("batches") != 1
        or response.get("paid_attempts") != 0
        or response.get("non_github_batches") != 0
    ):
        raise ObservationError("canary search included a forbidden provider")
    if response.get("legacy_delivery_intent") is not False:
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

    if run_binding_path is None:
        raise ObservationError("workflow identity binding path is required")
    if not isinstance(workflow_body, bytes) or not workflow_body:
        raise ObservationError("workflow body must be non-empty bytes")
    fixture = dict(build_canary_fixture(nonce))
    started = completed = _aware_now()
    maya = dict(fixture["maya_body"])
    maya["started_at"] = started
    maya["completed_at"] = completed
    fixture["maya_body"] = maya
    fixture["maya_body_sha256"] = canonical_hash(maya)
    maya_body = _wire_bytes(maya)
    if marker_dir.exists() and marker_dir.is_symlink():
        raise ObservationError("canary marker directory must not be a symlink")
    marker_dir.mkdir(parents=True, exist_ok=True)
    if not marker_dir.is_dir():
        raise ObservationError("canary marker directory must be a directory")
    marker_dir.chmod(0o700)
    if marker_dir.stat().st_mode & 0o7777 != 0o700:
        raise ObservationError("canary marker directory must have mode 0700")

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
    required_workflow = {
        "status",
        "run_id",
        "kind",
        "target",
        "request_sha256",
        "body_sha256",
    }
    if not required_workflow.issubset(workflow_response):
        raise ObservationError("workflow identity response is incomplete")
    if workflow_response.get("status") not in {200, 202} or not isinstance(
        workflow_response.get("run_id"), str
    ):
        raise ObservationError("workflow start did not return a bounded run projection")
    if workflow_response.get("kind") != "build-research-pack":
        raise ObservationError("workflow identity kind is not build-research-pack")
    if (
        not isinstance(workflow_response.get("target"), str)
        or not workflow_response["target"]
    ):
        raise ObservationError("workflow identity target is required")
    request_sha256 = workflow_response.get("request_sha256")
    body_sha256 = workflow_response.get("body_sha256")
    if (
        not isinstance(request_sha256, str)
        or request_sha256 == "0" * 64
        or len(request_sha256) != 64
        or any(char not in "0123456789abcdef" for char in request_sha256)
    ):
        raise ObservationError("workflow identity request hash is invalid")
    expected_body_sha256 = _wire_hash(workflow_body)
    if body_sha256 != expected_body_sha256:
        raise ObservationError("workflow identity body hash mismatch")
    try:
        bind_returned_run(
            run_binding_path,
            run_id=workflow_response["run_id"],
            kind=workflow_response["kind"],
            topic=workflow_response["target"],
            request_sha256=request_sha256,
            body_sha256=body_sha256,
            dispatched_at=started,
        )
    except ContractError as exc:
        raise ObservationError(f"workflow identity binding failed: {exc}") from exc
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
        "recovery": {
            "status": "not_applicable",
            "reason": "no mutation",
            "proof": "no mutation",
            "proof_sha256": "0" * 64,
        },
        "artifacts": {"status.json": b'{"status":"preflight_failed"}'},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--fixture", action="store_true", help="write a no-network preflight fixture"
    )
    parser.add_argument(
        "--adapter-module",
        help="import a reviewed module exposing build_adapters(); never a live default",
    )
    args = parser.parse_args(argv)
    if args.fixture and args.adapter_module:
        parser.error("--fixture and --adapter-module are mutually exclusive")
    if args.fixture:
        write_bundle(args.output, _fixture_payload())
        return 0
    if not args.adapter_module:
        # Fail closed with a machine-readable artifact instead of pretending
        # that the harness performed a production cycle.
        write_bundle(
            args.output,
            _failure_payload("preflight_failed", "adapter injection required"),
        )
        return 2
    module = importlib.import_module(args.adapter_module)
    factory = getattr(module, "build_adapters", None)
    if not callable(factory):
        parser.error("adapter module must expose build_adapters()")
    adapters = factory()
    result = execute_cycle(
        adapters,
        output=args.output,
        nonce="cli-injected-nonce",
        marker_dir=args.output.parent / f".{args.output.name}.markers",
        workflow_body=b'{"topic":"argus-acceptance-v3"}',
        run_binding_path=args.output.parent / f".{args.output.name}.run.json",
    )
    return 0 if result.get("verdict") == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
