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
import inspect
import json
from pathlib import Path
import time
from typing import Any, Mapping, Protocol

from argus.acceptance_v3.bundle import (
    build_canary_fixture,
    verify_bundle,
    write_bundle,
)
from argus.acceptance_v3.contract import (
    ContractError,
    GLOBAL_GUARD_PATH,
    bind_returned_run,
    canonical_hash,
    create_global_guard,
    validate_execution_contract,
    write_execution_contract,
    write_phase_marker,
)
from argus.acceptance_v3.observations import (
    ObservationError,
    normalize_transport_envelope,
    replay_capture_evidence,
    validate_preflight_observations,
    validate_snapshot_projection,
    validate_transport_pages,
)


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

    def read_artifact(
        self, run_id: str, kind: str, transport: str
    ) -> Mapping[str, Any]: ...

    def evaluate(self, artifact: bytes) -> Mapping[str, Any]: ...

    def rollback(self, reason: str) -> Mapping[str, Any]: ...


CLIENT_DEADLINE_SECONDS = 600.0
WORKFLOW_DEADLINE_SECONDS = 540.0
STATUS_REQUEST_TIMEOUT_SECONDS = 120.0
MAX_STATUS_POLLS = 600
_PENDING_STATUSES = frozenset({"pending", "running", "in_progress", "queued"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "timeout", "timed_out"})


@dataclass(frozen=True)
class CanaryResult:
    fixture: Mapping[str, Any]
    search_response: Mapping[str, Any]
    maya_evidence: Mapping[str, Any]
    workflow_response: Mapping[str, Any]


def _failure_payload(
    status: str,
    reason: str,
    *,
    artifact: bytes | None = None,
    artifacts: Mapping[str, bytes] | None = None,
    recovery: Mapping[str, Any] | None = None,
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
    output_artifacts: dict[str, bytes] = {
        "status.json": json.dumps({"status": status}).encode()
    }
    if artifacts is not None:
        for name, value in artifacts.items():
            if not isinstance(name, str) or not isinstance(value, bytes):
                raise ValueError("failure artifact map is invalid")
            output_artifacts[name] = value
    elif status == "evaluator_not_run" and artifact is not None:
        output_artifacts["report.json"] = artifact
    recovery_document = dict(recovery or _no_change_recovery(reason))
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
        "recovery": recovery_document,
        "artifacts": output_artifacts,
    }


def _no_change_recovery(reason: str) -> dict[str, Any]:
    """Return the only valid no-mutation recovery document.

    This is used exclusively by preflight branches.  Once a canary or start
    request has been dispatched, callers must use the rollback receipt helper
    below; emitting this document would falsely claim that no state changed.
    """

    proof = f"no mutation before side effects: {reason}"
    return {
        "status": "not_applicable",
        "reason": reason,
        "proof": proof,
        "proof_sha256": canonical_hash(proof),
        "no_change": True,
        "change_count": 0,
        "before_sha256": "0" * 64,
        "after_sha256": "0" * 64,
    }


def _rollback_recovery(
    rollback: object, reason: str, *, baseline: Mapping[str, Any] | None = None
) -> tuple[bool, dict[str, Any]]:
    """Normalize a caller rollback receipt without inventing restoration proof."""

    if not isinstance(rollback, Mapping):
        proof = f"rollback receipt unavailable: {reason}"
        return False, {
            "status": "failed",
            "reason": reason,
            "proof": proof,
            "proof_sha256": canonical_hash(proof),
            "no_change": False,
        }
    proof = rollback.get("proof")
    if not isinstance(proof, str) or not proof:
        proof = f"rollback receipt incomplete: {reason}"
    proof_hash = rollback.get("proof_sha256")
    proof_hash_valid = isinstance(proof_hash, str) and proof_hash == canonical_hash(
        proof
    )
    complete = rollback.get("status") == "complete" and proof_hash_valid
    required_hashes = (
        "backup_sha256",
        "restore_sha256",
        "schema_sha256",
        "identity_sha256",
        "soak_sha256",
    )
    if complete and not all(
        isinstance(rollback.get(key), str)
        and len(rollback[key]) == 64
        and all(char in "0123456789abcdef" for char in rollback[key])
        for key in required_hashes + ("before_sha256", "after_sha256")
    ):
        complete = False
    baseline_evidence = rollback.get("baseline")
    quiescence = rollback.get("quiescence")
    if complete:
        if not isinstance(baseline_evidence, Mapping) or not isinstance(
            quiescence, Mapping
        ):
            complete = False
        else:
            baseline_keys = {
                "source_revision",
                "image_digest",
                "deployment_identity",
                "release_receipt_sha256",
            }
            if set(baseline_evidence) != baseline_keys:
                complete = False
            if (
                not isinstance(baseline_evidence.get("source_revision"), str)
                or len(baseline_evidence["source_revision"]) != 40
                or any(
                    char not in "0123456789abcdef"
                    for char in baseline_evidence["source_revision"]
                )
                or not isinstance(baseline_evidence.get("image_digest"), str)
                or not baseline_evidence["image_digest"].startswith("sha256:")
                or len(baseline_evidence["image_digest"]) != 71
                or not isinstance(baseline_evidence.get("deployment_identity"), str)
                or not baseline_evidence["deployment_identity"]
                or not isinstance(baseline_evidence.get("release_receipt_sha256"), str)
                or len(baseline_evidence["release_receipt_sha256"]) != 64
                or any(
                    char not in "0123456789abcdef"
                    for char in baseline_evidence["release_receipt_sha256"]
                )
            ):
                complete = False
            if baseline is not None and dict(baseline_evidence) != dict(baseline):
                complete = False
            proof = quiescence.get("proof")
            proof_sha256 = quiescence.get("proof_sha256")
            if (
                quiescence.get("status") not in {"complete", "quiescent"}
                or not isinstance(proof, str)
                or not proof
                or proof_sha256 != canonical_hash(proof)
                or quiescence.get("pending") != 0
                or quiescence.get("retry") != 0
                or isinstance(quiescence.get("dead_letter"), bool)
                or not isinstance(quiescence.get("dead_letter"), int)
                or quiescence.get("dead_letter") < 0
            ):
                complete = False
    document: dict[str, Any] = {
        "status": "complete" if complete else "failed",
        "reason": str(rollback.get("reason") or reason),
        "proof": proof,
        "proof_sha256": canonical_hash(proof),
        "no_change": False,
    }
    if isinstance(rollback.get("change_count"), int) and not isinstance(
        rollback["change_count"], bool
    ):
        document["change_count"] = rollback["change_count"]
    for key in required_hashes + ("before_sha256", "after_sha256"):
        value = rollback.get(key)
        if (
            isinstance(value, str)
            and len(value) == 64
            and all(char in "0123456789abcdef" for char in value)
        ):
            document[key] = value
    if isinstance(baseline_evidence, Mapping):
        document["baseline"] = dict(baseline_evidence)
    if isinstance(quiescence, Mapping):
        document["quiescence"] = dict(quiescence)
    return complete, document


def _artifact_reader_signature_valid(adapters: object) -> bool:
    """Require the kind-aware status/report/manifest adapter seam."""

    reader = getattr(adapters, "read_artifact", None)
    if not callable(reader):
        return False
    try:
        parameters = list(inspect.signature(reader).parameters.values())
    except (TypeError, ValueError):
        return False
    positional = [
        parameter
        for parameter in parameters
        if parameter.kind
        in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    return len(positional) >= 3 or any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in parameters
    )


def _read_bound_artifact(
    adapters: CycleAdapters,
    run_id: str,
    kind: str,
    transport: str | None = None,
) -> bytes:
    """Read one kind-aware transport projection and reconstruct exact bytes."""

    if transport is None:
        # Retain the old private helper's call shape for downstream fixtures;
        # the cycle itself always supplies an explicit kind and transport.
        transport = kind
        kind = "report"
    projection = normalize_transport_envelope(
        adapters.read_artifact(run_id, kind, transport)
    )
    if kind not in {"report", "manifest"}:
        raise ValueError("artifact kind is not report or manifest")
    pages = projection.get("pages")
    expected_sha256 = projection.get("sha256")
    if not isinstance(pages, list) or not isinstance(expected_sha256, str):
        raise ValueError("artifact projection is missing pages/hash")
    validate_transport_pages(
        pages,
        expected_sha256=expected_sha256,
        expected_total_bytes=projection.get("total_bytes", projection.get("bytes")),
        expected_terminal_offset=projection.get(
            "terminal_offset", projection.get("terminal_bytes")
        ),
    )
    try:
        return "".join(page["data"] for page in pages).encode("utf-8")
    except (KeyError, TypeError, UnicodeError) as exc:
        raise ValueError("artifact projection page data is invalid") from exc


def _semantic_artifacts_equal(left: bytes, right: bytes) -> bool:
    """Compare normalized artifact payloads, not HTTP/MCP envelope bytes."""

    if left == right:
        return True
    try:
        left_value = json.loads(left.decode("utf-8"))
        right_value = json.loads(right.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return canonical_hash(left_value) == canonical_hash(right_value)


def _status_semantics(value: Mapping[str, Any]) -> str:
    """Hash only the normalized workflow status projection.

    HTTP response metadata, MCP JSON-RPC/session wrappers, and adapter trace
    fields are deliberately excluded before comparison.  The workflow body
    itself remains fully bound by the run ID and status identity fields.
    """

    ignored = {
        "http_status",
        "status_code",
        "headers",
        "session_id",
        "jsonrpc_id",
        "transport",
        "content_type",
    }
    return canonical_hash(
        {key: value for key, value in value.items() if key not in ignored}
    )


def _read_bound_status(
    adapters: CycleAdapters, run_id: str, transport: str
) -> Mapping[str, Any]:
    projection = normalize_transport_envelope(
        adapters.read_artifact(run_id, "status", transport)
    )
    if projection.get("run_id") != run_id:
        raise ValueError("status projection run identity mismatch")
    if projection.get("status") not in _PENDING_STATUSES | _TERMINAL_STATUSES:
        raise ValueError("status projection has an invalid workflow state")
    return projection


def _require_status_artifact_binding(
    status: Mapping[str, Any], *, kind: str, artifact: bytes
) -> None:
    """Bind reconstructed bytes to the terminal status artifact metadata."""

    metadata: Mapping[str, Any] | None = None
    artifacts = status.get("artifacts")
    if isinstance(artifacts, list):
        for candidate in artifacts:
            if isinstance(candidate, Mapping) and candidate.get("kind") == kind:
                metadata = candidate
                break
    if metadata is None and isinstance(status.get(f"{kind}_sha256"), str):
        metadata = {
            "sha256": status.get(f"{kind}_sha256"),
            "total_bytes": status.get(f"{kind}_bytes"),
        }
    if metadata is None:
        raise ValueError(f"status projection is missing {kind} metadata")
    expected_sha256 = metadata.get("sha256")
    if expected_sha256 != hashlib.sha256(artifact).hexdigest():
        raise ValueError(f"{kind} hash does not match status projection")
    total_bytes = metadata.get("total_bytes", metadata.get("size_bytes"))
    if total_bytes is not None and total_bytes != len(artifact):
        raise ValueError(f"{kind} byte count does not match status projection")


def _verify_evaluator_result(
    evaluation: Mapping[str, Any],
    execution_contract: Mapping[str, Any],
    artifact: bytes,
) -> None:
    expected = execution_contract.get("evaluator")
    actual = evaluation.get("evaluator")
    if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
        raise ValueError("evaluator identity is missing")
    for key in (
        "version",
        "model",
        "reasoning_effort",
        "sampling",
        "web_enabled",
        "tools_enabled",
        "memory_enabled",
        "provider_enabled",
        "database_enabled",
        "spend_authority",
        "prompt_sha256",
        "prompt_bytes_sha256",
        "settings_sha256",
        "run_receipt_sha256",
    ):
        if actual.get(key) != expected.get(key):
            raise ValueError(f"evaluator identity mismatch: {key}")
    if evaluation.get("artifact_sha256") != hashlib.sha256(artifact).hexdigest():
        raise ValueError("evaluator artifact receipt mismatch")
    artifact_hashes = execution_contract.get("artifact_hashes")
    if not isinstance(artifact_hashes, Mapping) or evaluation.get(
        "evaluator_sha256"
    ) != artifact_hashes.get("evaluator_sha256"):
        raise ValueError("evaluator implementation hash is not contract-bound")
    capabilities = actual.get("capabilities")
    if capabilities is not None:
        if not isinstance(capabilities, Mapping):
            raise ValueError("evaluator capabilities projection is invalid")
        for key in (
            "web",
            "tools",
            "memory",
            "provider",
            "database",
            "spend",
        ):
            if capabilities.get(key, False) is not False:
                raise ValueError("evaluator capability isolation is incomplete")
    if (
        any(
            actual.get(key) is not False
            for key in (
                "web_enabled",
                "tools_enabled",
                "memory_enabled",
                "provider_enabled",
                "database_enabled",
            )
        )
        or actual.get("spend_authority") != "none"
    ):
        raise ValueError("evaluator capability isolation is incomplete")


def _invoke_poll(
    adapters: CycleAdapters, run_id: str, timeout: float
) -> Mapping[str, Any]:
    """Call an injected status adapter with its finite request timeout.

    Test and production adapters have historically exposed both a one-argument
    and a keyword-timeout form.  Supporting both keeps the seam small while
    ensuring a reviewed transport can never silently omit its deadline.
    """

    poll = adapters.poll_status
    try:
        parameters = inspect.signature(poll).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "timeout_seconds" in parameters:
        value = poll(run_id, timeout_seconds=timeout)
    elif "timeout" in parameters:
        value = poll(run_id, timeout=timeout)
    else:
        value = poll(run_id)
    if not isinstance(value, Mapping):
        raise ValueError("workflow status projection is not an object")
    return value


def _workflow_time(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"workflow {label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"workflow {label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"workflow {label} must be aware UTC")
    if parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"workflow {label} must be UTC")
    return parsed.astimezone(timezone.utc)


def _validate_workflow_deadline(terminal: Mapping[str, Any]) -> None:
    """Require the workflow's persisted interval to fit the 540s bound."""

    started = terminal.get("started_at", terminal.get("created_at"))
    finished = terminal.get("finished_at", terminal.get("completed_at"))
    if started is None and finished is None:
        # The adapter may expose only a terminal status in a hermetic unit
        # fixture.  The client monotonic deadline still bounds that call.
        return
    if started is None or finished is None:
        raise ValueError("workflow terminal interval is incomplete")
    elapsed = (
        _workflow_time(finished, "finished_at") - _workflow_time(started, "started_at")
    ).total_seconds()
    if elapsed < 0 or elapsed > WORKFLOW_DEADLINE_SECONDS:
        raise TimeoutError("workflow exceeded the 540-second deadline")


def _poll_workflow(
    adapters: CycleAdapters,
    run_id: str,
    *,
    dispatched_monotonic: float | None = None,
) -> Mapping[str, Any]:
    """Poll a run with finite client/request deadlines and no side-effect retry."""

    start = time.monotonic() if dispatched_monotonic is None else dispatched_monotonic
    client_deadline = start + CLIENT_DEADLINE_SECONDS
    for _ in range(MAX_STATUS_POLLS):
        remaining = client_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("workflow client deadline exceeded")
        terminal = _invoke_poll(
            adapters, run_id, min(STATUS_REQUEST_TIMEOUT_SECONDS, remaining)
        )
        status = terminal.get("status")
        if status in _TERMINAL_STATUSES:
            _validate_workflow_deadline(terminal)
            return terminal
        if status not in _PENDING_STATUSES:
            raise ValueError("workflow status is outside the frozen terminal set")
    raise TimeoutError("workflow poll loop exhausted its bounded attempts")


def _rollback_and_publish(
    adapters: CycleAdapters,
    *,
    status: str,
    reason: str,
    output: Path,
    artifact: bytes | None = None,
    artifacts: Mapping[str, bytes] | None = None,
    baseline: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Perform exactly one recovery attempt and publish its proof."""

    try:
        receipt = adapters.rollback(reason)
    except Exception as exc:
        receipt = {
            "status": "failed",
            "reason": f"rollback adapter failed: {type(exc).__name__}",
            "proof": f"rollback adapter failed: {type(exc).__name__}",
        }
    complete, recovery = _rollback_recovery(receipt, reason, baseline=baseline)
    terminal_status = status if complete else "rollback_incomplete"
    payload = _failure_payload(
        terminal_status,
        reason if complete else str(recovery.get("reason", reason)),
        artifact=artifact,
        artifacts=artifacts,
        recovery=recovery,
    )
    write_bundle(output, payload)
    return verify_bundle(output)


_REQUIRED_PREFLIGHT_OBSERVATIONS = frozenset(
    {
        "spend",
        "balance",
        "outbox",
        "policy",
        "logs",
        "unauth_probe",
        "audit",
        "network",
    }
)


def _preflight_observations_ready(preflight: Mapping[str, Any]) -> bool:
    """Require every no-spend/authority predicate before any canary POST."""

    observations = preflight.get("observations", preflight.get("evidence"))
    if not isinstance(observations, Mapping):
        return False
    if set(observations) != _REQUIRED_PREFLIGHT_OBSERVATIONS:
        return False
    try:
        validate_preflight_observations(observations)
    except ObservationError:
        return False
    return True


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

    def publish(
        status: str,
        reason: str,
        *,
        artifact: bytes | None = None,
        artifacts: Mapping[str, bytes] | None = None,
        recovery: Mapping[str, Any] | None = None,
    ):
        write_bundle(
            output,
            _failure_payload(
                status,
                reason,
                artifact=artifact,
                artifacts=artifacts,
                recovery=recovery,
            ),
        )
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
        execution_contract = validate_execution_contract(execution_contract)
    except ContractError as exc:
        return publish(
            "preflight_failed",
            f"execution contract is not frozen: {type(exc).__name__}",
        )
    if not _preflight_observations_ready(preflight):
        return publish(
            "preflight_failed",
            "required spend/balance/outbox/policy/log/unauth/audit evidence is incomplete",
        )
    if (
        guard_path != GLOBAL_GUARD_PATH
        or execution_contract.get("guard_path") != GLOBAL_GUARD_PATH
    ):
        return publish(
            "preflight_failed", "preflight guard path is not the frozen global guard"
        )
    evidence_root_value = execution_contract.get("evidence_root")
    if not isinstance(evidence_root_value, str):
        return publish(
            "preflight_failed", "execution contract evidence root is missing"
        )
    evidence_root = Path(evidence_root_value)
    expected_contract_path = evidence_root / "execution-contract.json"
    expected_marker_dir = evidence_root / "markers"
    expected_binding_path = evidence_root / "returned-run.json"
    expected_output = evidence_root / "bundle"
    if (
        Path(contract_path) != expected_contract_path
        or Path(contract_path).resolve(strict=False) != Path(contract_path)
        or marker_dir != expected_marker_dir
        or run_binding_path != expected_binding_path
        or output != expected_output
    ):
        return publish(
            "preflight_failed",
            "cycle paths are not fixed descendants of the evidence root",
        )
    for name in ("snapshot", "poll_status", "read_artifact", "evaluate", "rollback"):
        if not callable(getattr(adapters, name, None)):
            return publish("preflight_failed", f"required adapter missing: {name}")
    if not _artifact_reader_signature_valid(adapters):
        return publish(
            "preflight_failed",
            "artifact reader must accept run_id, kind, and transport",
        )
    try:
        pre_snapshot = adapters.snapshot("pre_canary")
        if not isinstance(pre_snapshot, Mapping):
            return publish("preflight_failed", "pre-canary snapshot is not an object")
        snapshots = execution_contract.get("snapshots")
        if not isinstance(snapshots, Mapping):
            return publish(
                "preflight_failed", "pre-canary snapshot is not contract-bound"
            )
        validate_snapshot_projection(
            pre_snapshot, expected_sha256=snapshots.get("pre_canary_sha256")
        )
        start_body = execution_contract.get("start_body")
        expected_request_sha256 = execution_contract.get("request_sha256")
        if not isinstance(start_body, Mapping) or not isinstance(
            expected_request_sha256, str
        ):
            return publish(
                "preflight_failed", "execution contract start identity is incomplete"
            )
        if set(start_body) != set(execution_contract["request"]):
            return publish("preflight_failed", "request and start-body key sets differ")
        derived_workflow_body = _wire_bytes(start_body)
        if workflow_body != derived_workflow_body:
            return publish(
                "preflight_failed",
                "caller workflow body does not match the immutable start body",
            )
    except Exception as exc:
        return publish(
            "preflight_failed", f"preflight snapshot failed: {type(exc).__name__}"
        )
    guard_preparation_started = False
    try:
        guard_preparation_started = True
        prepare_guard = getattr(adapters, "prepare_guard", None)
        if callable(prepare_guard):
            advisory = prepare_guard(
                execution_contract,
                contract_path=Path(contract_path),
                guard_path=Path(guard_path),
            )
            if not isinstance(advisory, Mapping):
                raise ContractError("injected guard preparation returned no receipt")
        # The harness, never an injected adapter, owns the immutable writes.
        # An adapter may provide read-only planning above, but it cannot fake
        # O_EXCL/fsync evidence or bypass the exact global guard.
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
        if guard_preparation_started:
            return _rollback_and_publish(
                adapters,
                status="preflight_failed",
                reason=f"contract/guard preparation failed: {type(exc).__name__}",
                output=output,
                baseline=execution_contract.get("rollback"),
            )
        return publish(
            "preflight_failed",
            f"contract/guard preparation failed: {type(exc).__name__}",
        )
    try:
        side_effects_started = True
        canary = dispatch_canary(
            adapters,
            nonce=nonce,
            marker_dir=marker_dir,
            workflow_body=derived_workflow_body,
            run_binding_path=run_binding_path,
            expected_request_sha256=expected_request_sha256,
            expected_canary=execution_contract.get("canary"),
            canary_timestamp=execution_contract["snapshots"]["observed_at"],
            require_safe_start=True,
        )
        post_canary_snapshot = adapters.snapshot("post_canary")
        if not isinstance(post_canary_snapshot, Mapping):
            raise ValueError("post-canary snapshot is not an object")
        validate_snapshot_projection(post_canary_snapshot)
        run_id = str(canary.workflow_response["run_id"])
        try:
            terminal = _poll_workflow(adapters, run_id)
        except (TimeoutError, ValueError, ObservationError) as exc:
            return _rollback_and_publish(
                adapters,
                status="pre_artifact_not_run",
                reason=f"workflow polling terminated: {type(exc).__name__}",
                output=output,
                baseline=execution_contract.get("rollback"),
            )
        http_status = _read_bound_status(adapters, run_id, "http")
        mcp_status = _read_bound_status(adapters, run_id, "mcp")
        if _status_semantics(http_status) != _status_semantics(mcp_status):
            raise ValueError("HTTP/MCP status semantics differ")
        if http_status.get("status") != terminal.get("status"):
            raise ValueError("status artifact does not match terminal poll")
        if terminal.get("status") != "completed":
            return _rollback_and_publish(
                adapters,
                status="pre_artifact_not_run",
                reason=f"workflow terminal status: {terminal.get('status')}",
                output=output,
                baseline=execution_contract.get("rollback"),
            )
        completed_artifacts: dict[str, bytes] = {}
        for kind in ("report", "manifest"):
            http_artifact = _read_bound_artifact(adapters, run_id, kind, "http")
            mcp_artifact = _read_bound_artifact(adapters, run_id, kind, "mcp")
            if not http_artifact or http_artifact != mcp_artifact:
                raise ValueError(
                    f"HTTP/MCP {kind} artifact did not reconstruct identical bytes"
                )
            _require_status_artifact_binding(
                http_status, kind=kind, artifact=http_artifact
            )
            _require_status_artifact_binding(
                mcp_status, kind=kind, artifact=mcp_artifact
            )
            completed_artifacts[f"{kind}.json"] = http_artifact
        post_benchmark_snapshot = adapters.snapshot("post_benchmark")
        if not isinstance(post_benchmark_snapshot, Mapping):
            raise ValueError("post-benchmark snapshot is not an object")
        validate_snapshot_projection(post_benchmark_snapshot)
        try:
            evaluation = adapters.evaluate(completed_artifacts["report.json"])
        except Exception as exc:
            return _rollback_and_publish(
                adapters,
                status="evaluator_not_run",
                reason=f"evaluator unavailable: {type(exc).__name__}",
                output=output,
                artifacts=completed_artifacts,
                baseline=execution_contract.get("rollback"),
            )
        if not isinstance(evaluation, Mapping) or evaluation.get("status") != "scored":
            return _rollback_and_publish(
                adapters,
                status="evaluator_not_run",
                reason="evaluator did not return a scored identity",
                output=output,
                artifacts=completed_artifacts,
                baseline=execution_contract.get("rollback"),
            )
        try:
            _verify_evaluator_result(
                evaluation, execution_contract, completed_artifacts["report.json"]
            )
        except Exception as exc:
            return _rollback_and_publish(
                adapters,
                status="evaluator_not_run",
                reason=f"evaluator identity rejected: {type(exc).__name__}",
                output=output,
                artifacts=completed_artifacts,
                baseline=execution_contract.get("rollback"),
            )
        payload = evaluation.get("bundle_payload")
        if not isinstance(payload, Mapping):
            return _rollback_and_publish(
                adapters,
                status="evaluator_not_run",
                reason="evaluator returned no bound bundle payload",
                output=output,
                artifacts=completed_artifacts,
                baseline=execution_contract.get("rollback"),
            )
        score_document = payload.get("score")
        score_evaluator = (
            score_document.get("evaluator")
            if isinstance(score_document, Mapping)
            else None
        )
        if not isinstance(score_evaluator, Mapping):
            return _rollback_and_publish(
                adapters,
                status="evaluator_not_run",
                reason="bundle score is missing evaluator identity",
                output=output,
                artifacts=completed_artifacts,
                baseline=execution_contract.get("rollback"),
            )
        for key in (
            "model",
            "prompt_sha256",
            "settings_sha256",
            "run_receipt_sha256",
        ):
            if score_evaluator.get(key) != execution_contract["evaluator"].get(key):
                return _rollback_and_publish(
                    adapters,
                    status="evaluator_not_run",
                    reason=f"bundle evaluator identity mismatch: {key}",
                    output=output,
                    artifacts=completed_artifacts,
                    baseline=execution_contract.get("rollback"),
                )
        write_bundle(output, payload)
        return verify_bundle(output)
    except (ObservationError, ContractError, ValueError) as exc:
        if side_effects_started:
            return _rollback_and_publish(
                adapters,
                status="FAIL",
                reason=f"cycle contract rejected: {type(exc).__name__}",
                output=output,
                baseline=execution_contract.get("rollback"),
            )
        return publish(
            "preflight_failed", f"cycle contract rejected: {type(exc).__name__}"
        )
    except Exception as exc:
        if side_effects_started:
            return _rollback_and_publish(
                adapters,
                status="FAIL",
                reason=f"cycle failed: {type(exc).__name__}",
                output=output,
                baseline=execution_contract.get("rollback"),
            )
        return publish("preflight_failed", f"cycle failed: {type(exc).__name__}")


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


def _require_search_canary(
    response: Mapping[str, Any], *, expected_body_sha256: str
) -> None:
    required = {
        "status",
        "cached",
        "caller",
        "body_sha256",
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
    if response.get("caller") != "mac-agents":
        raise ObservationError("canary search caller identity is not mac-agents")
    if response.get("body_sha256") != expected_body_sha256:
        raise ObservationError("canary search body hash mismatch")
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
    expected_request_sha256: str | None = None,
    expected_canary: Mapping[str, Any] | None = None,
    canary_timestamp: str | None = None,
    require_safe_start: bool = False,
) -> CanaryResult:
    """Dispatch the exact three canary POSTs and one start POST once each."""

    if run_binding_path is None:
        raise ObservationError("workflow identity binding path is required")
    if not isinstance(workflow_body, bytes) or not workflow_body:
        raise ObservationError("workflow body must be non-empty bytes")
    try:
        parsed_workflow = json.loads(workflow_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObservationError("workflow body must be canonical UTF-8 JSON") from exc
    if not isinstance(parsed_workflow, Mapping):
        raise ObservationError("workflow body must be a JSON object")
    # Resolve one timestamp before constructing the fixture.  The timestamp is
    # part of the canonical Maya body, so deriving it twice would silently
    # break the frozen contract hash while still issuing a valid request.
    final_timestamp = canary_timestamp or _aware_now()
    fixture = dict(
        build_canary_fixture(
            nonce,
            started_at=final_timestamp,
            completed_at=final_timestamp,
        )
    )
    maya = dict(fixture["maya_body"])
    maya_body = _wire_bytes(maya)
    actual_canary_hashes = {
        "query_sha256": canonical_hash(fixture["query"]),
        "search_body_sha256": canonical_hash(fixture["search_body"]),
        "maya_body_sha256": canonical_hash(maya),
        "idempotency_key_sha256": canonical_hash(maya["idempotency_key"]),
    }
    if expected_canary is not None:
        required_canary = {
            "query_sha256",
            "search_body_sha256",
            "maya_body_sha256",
            "idempotency_key_sha256",
        }
        if (
            set(expected_canary) != required_canary
            or dict(expected_canary) != actual_canary_hashes
        ):
            raise ObservationError("final canary wire bodies are not contract-bound")
    fixture.update(actual_canary_hashes)
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
    _require_search_canary(
        search_response,
        expected_body_sha256=canonical_hash(fixture["search_body"]),
    )

    write_phase_marker(
        marker_dir / "maya-first.json",
        phase="maya-first",
        identity={"body_sha256": actual_canary_hashes["maya_body_sha256"]},
    )
    first = adapters.post_maya(maya_body)
    write_phase_marker(
        marker_dir / "maya-replay.json",
        phase="maya-replay",
        identity={"body_sha256": actual_canary_hashes["maya_body_sha256"]},
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
    raw_status = workflow_response.get("status")
    http_status = workflow_response.get(
        "http_status", workflow_response.get("status_code")
    )
    if http_status is None and isinstance(raw_status, int):
        http_status = raw_status
    workflow_status = workflow_response.get("workflow_status", raw_status)
    if require_safe_start:
        if http_status != 202 or workflow_status != "pending":
            raise ObservationError("workflow start must return HTTP 202/pending")
    elif http_status not in {200, 202} and workflow_status not in {"pending", "queued"}:
        raise ObservationError("workflow start did not return a bounded run projection")
    if not isinstance(workflow_response.get("run_id"), str):
        raise ObservationError("workflow start did not return a bounded run projection")
    if workflow_response.get("kind") != "build-research-pack":
        raise ObservationError("workflow identity kind is not build-research-pack")
    if (
        not isinstance(workflow_response.get("target"), str)
        or not workflow_response["target"]
    ):
        raise ObservationError("workflow identity target is required")
    if require_safe_start:
        created_at = workflow_response.get("created_at")
        _workflow_time(created_at, "created_at")
        status_url = workflow_response.get("status_url")
        if (
            not isinstance(status_url, str)
            or not status_url.startswith("/api/workflows/")
            or workflow_response["run_id"] not in status_url
            or "?" in status_url
            or "#" in status_url
        ):
            raise ObservationError("workflow start status_url is unsafe")
    request_sha256 = workflow_response.get("request_sha256")
    body_sha256 = workflow_response.get("body_sha256")
    if (
        not isinstance(request_sha256, str)
        or request_sha256 == "0" * 64
        or len(request_sha256) != 64
        or any(char not in "0123456789abcdef" for char in request_sha256)
    ):
        raise ObservationError("workflow identity request hash is invalid")
    if (
        expected_request_sha256 is not None
        and request_sha256 != expected_request_sha256
    ):
        raise ObservationError("workflow identity request hash mismatch")
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
            dispatched_at=final_timestamp,
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
            "proof_sha256": canonical_hash("no mutation"),
            "no_change": True,
            "change_count": 0,
            "before_sha256": "0" * 64,
            "after_sha256": "0" * 64,
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
