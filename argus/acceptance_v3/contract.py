"""Canonical execution identity and one-shot filesystem guards.

All functions in this module are local and deterministic.  They accept values
already obtained by an authorized caller; they do not resolve credentials or
contact an authority.  Evidence files are created with ``O_EXCL`` and fsynced
before a caller is allowed to dispatch a side-effecting request.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


CYCLE_ID = "argus-acceptance-v3-free-targets-2026-08-10"
PROFILE = "free"
SCHEMA = "build-research-pack/v3"
GLOBAL_GUARD_PATH = (
    "/Users/macmini/.local/state/argus-tonight-final-score-v3-started.json"
)
EVIDENCE_ROOT_MODE = 0o700
EVIDENCE_FILE_MODE = 0o600

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class ContractError(ValueError):
    """The immutable execution contract or guard is invalid."""


def canonical_bytes(value: Any) -> bytes:
    """Return the frozen compact JSON representation of ``value``.

    ``allow_nan=False`` is intentional: non-finite numbers would produce a
    different parser-dependent contract and are never valid evidence.
    """

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ContractError(f"value is not canonical JSON: {exc}") from exc


def canonical_hash(value: Any) -> str:
    """Return the SHA-256 of :func:`canonical_bytes`."""

    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContractError(f"{label} must be a lowercase SHA-256")
    return value


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{label} must be an aware UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{label} must be an aware UTC timestamp") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        raise ContractError(f"{label} must be an aware UTC timestamp")
    return value


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise ContractError(f"{label} must be an object with string keys")
    return dict(value)


def _exact_keys(value: Mapping[str, Any], required: set[str], label: str) -> None:
    if set(value) != required:
        missing = sorted(required - set(value))
        extra = sorted(set(value) - required)
        raise ContractError(
            f"{label} identity fields mismatch (missing={missing}, extra={extra})"
        )


def _validate_identity_hashes(value: Mapping[str, Any], label: str) -> None:
    for key, item in value.items():
        if key.endswith("_sha256"):
            _sha(item, f"{label}.{key}")


def _validate_candidate(value: object) -> dict[str, Any]:
    candidate = _mapping(value, "candidate")
    required = {
        "version",
        "source_revision",
        "image_digest",
        "deployment_identity",
        "release_receipt_sha256",
        "hermetic_bundle_sha256",
        "live_config_sha256",
        "promotion_receipt_sha256",
        "runtime_manifest_sha256",
    }
    _exact_keys(candidate, required, "candidate")
    if candidate["version"] != "1.6.4":
        raise ContractError("candidate version must be 1.6.4")
    if not isinstance(candidate["source_revision"], str) or not _COMMIT.fullmatch(
        candidate["source_revision"]
    ):
        raise ContractError("candidate source_revision must be a full commit")
    if not isinstance(candidate["image_digest"], str) or not _DIGEST.fullmatch(
        candidate["image_digest"]
    ):
        raise ContractError("candidate image_digest must be immutable")
    if (
        not isinstance(candidate["deployment_identity"], str)
        or not candidate["deployment_identity"]
    ):
        raise ContractError("candidate deployment_identity is required")
    _validate_identity_hashes(candidate, "candidate")
    return candidate


def _validate_rollback(value: object) -> dict[str, Any]:
    rollback = _mapping(value, "rollback")
    required = {
        "source_revision",
        "image_digest",
        "deployment_identity",
        "release_receipt_sha256",
    }
    _exact_keys(rollback, required, "rollback")
    if not isinstance(rollback["source_revision"], str) or not _COMMIT.fullmatch(
        rollback["source_revision"]
    ):
        raise ContractError("rollback source_revision must be a full commit")
    if not isinstance(rollback["image_digest"], str) or not _DIGEST.fullmatch(
        rollback["image_digest"]
    ):
        raise ContractError("rollback image_digest must be immutable")
    if (
        not isinstance(rollback["deployment_identity"], str)
        or not rollback["deployment_identity"]
    ):
        raise ContractError("rollback deployment_identity is required")
    _sha(rollback["release_receipt_sha256"], "rollback.release_receipt_sha256")
    return rollback


def _validate_evaluator(value: object) -> dict[str, Any]:
    evaluator = _mapping(value, "evaluator")
    required = {
        "model",
        "reasoning_effort",
        "prompt_sha256",
        "settings_sha256",
        "run_receipt_sha256",
    }
    _exact_keys(evaluator, required, "evaluator")
    if evaluator["model"] != "gpt-5.6-sol" or evaluator["reasoning_effort"] != "xhigh":
        raise ContractError("evaluator identity is not the frozen evaluator")
    for key in ("prompt_sha256", "settings_sha256", "run_receipt_sha256"):
        _sha(evaluator[key], f"evaluator.{key}")
    return evaluator


def _validate_hash_map(value: object, label: str, required: set[str]) -> dict[str, Any]:
    mapping = _mapping(value, label)
    if not required.issubset(mapping):
        raise ContractError(f"{label} is missing required identity hashes")
    _validate_identity_hashes(mapping, label)
    return mapping


def _validate_artifact_hashes(value: object) -> dict[str, str]:
    hashes = _mapping(value, "artifact_hashes")
    required = {
        "spec_sha256",
        "scorecard_sha256",
        "synthesis_prompt_sha256",
        "evaluator_sha256",
        "harness_sha256",
        "client_probe_sha256",
    }
    _exact_keys(hashes, required, "artifact_hashes")
    for key, digest in hashes.items():
        _sha(digest, f"artifact_hashes.{key}")
    return {key: str(value) for key, value in hashes.items()}


def _validate_negative_probe(value: object) -> dict[str, str]:
    probe = _mapping(value, "negative_probe")
    required = {
        "request_sha256",
        "response_sha256",
        "before_snapshot_sha256",
        "after_snapshot_sha256",
    }
    _exact_keys(probe, required, "negative_probe")
    for key, digest in probe.items():
        _sha(digest, f"negative_probe.{key}")
    return {key: str(value) for key, value in probe.items()}


def validate_execution_contract(value: object) -> dict[str, Any]:
    """Validate and return a defensive copy of a persisted contract."""

    contract = _mapping(value, "execution contract")
    required = {
        "cycle_id",
        "profile",
        "schema",
        "request",
        "request_sha256",
        "start_body",
        "start_body_sha256",
        "endpoints",
        "endpoint_hashes",
        "candidate",
        "rollback",
        "evaluator",
        "snapshots",
        "canary",
        "artifact_hashes",
        "negative_probe",
        "topology",
        "policy",
        "corpus",
        "evidence_root",
        "guard_path",
    }
    _exact_keys(contract, required, "execution contract")
    if (
        contract["cycle_id"] != CYCLE_ID
        or contract["profile"] != PROFILE
        or contract["schema"] != SCHEMA
    ):
        raise ContractError("contract cycle/profile/schema does not match v3")
    request = _mapping(contract["request"], "request")
    start_body = _mapping(contract["start_body"], "start_body")
    if canonical_hash(request) != contract["request_sha256"]:
        raise ContractError("request hash mismatch")
    if canonical_hash(start_body) != contract["start_body_sha256"]:
        raise ContractError("start body hash mismatch")
    _sha(contract["request_sha256"], "request_sha256")
    _sha(contract["start_body_sha256"], "start_body_sha256")
    endpoints = _mapping(contract["endpoints"], "endpoints")
    endpoint_hashes = _mapping(contract["endpoint_hashes"], "endpoint_hashes")
    if set(endpoints) != set(endpoint_hashes):
        raise ContractError("endpoint hash set mismatch")
    for name, endpoint in endpoints.items():
        if not isinstance(endpoint, str) or not endpoint.startswith("/"):
            raise ContractError(f"endpoint {name} must be a relative API path")
        if endpoint_hashes[name] != canonical_hash(endpoint):
            raise ContractError(f"endpoint hash mismatch for {name}")
    _validate_candidate(contract["candidate"])
    _validate_rollback(contract["rollback"])
    _validate_evaluator(contract["evaluator"])
    _validate_hash_map(contract["snapshots"], "snapshots", {"pre_canary_sha256"})
    _validate_hash_map(
        contract["canary"],
        "canary",
        {"query_sha256", "body_sha256", "idempotency_key_sha256"},
    )
    _validate_artifact_hashes(contract["artifact_hashes"])
    _validate_negative_probe(contract["negative_probe"])
    for name in ("topology", "policy", "corpus"):
        _mapping(contract[name], name)
    for field in ("evidence_root", "guard_path"):
        if not isinstance(contract[field], str) or not contract[field]:
            raise ContractError(f"{field} is required")
    return contract


def build_execution_contract(
    *,
    request: Mapping[str, Any],
    start_body: Mapping[str, Any],
    endpoints: Mapping[str, str],
    candidate: Mapping[str, Any],
    rollback: Mapping[str, Any],
    evaluator: Mapping[str, Any],
    snapshots: Mapping[str, Any],
    canary: Mapping[str, Any],
    artifact_hashes: Mapping[str, str],
    negative_probe: Mapping[str, str],
    topology: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
    corpus: Mapping[str, Any] | None = None,
    evidence_root: str = "",
    guard_path: str = GLOBAL_GUARD_PATH,
) -> dict[str, Any]:
    """Build the exact v3 contract and validate all immutable identities.

    The caller supplies every hash that came from its preflight evidence.  The
    function computes request/body/endpoint hashes itself so a stale hash can
    never be admitted accidentally.
    """

    if not isinstance(request, Mapping) or not isinstance(start_body, Mapping):
        raise ContractError("request and start_body must be objects")
    endpoint_map = dict(endpoints)
    result = {
        "cycle_id": CYCLE_ID,
        "profile": PROFILE,
        "schema": SCHEMA,
        "request": dict(request),
        "request_sha256": canonical_hash(request),
        "start_body": dict(start_body),
        "start_body_sha256": canonical_hash(start_body),
        "endpoints": endpoint_map,
        "endpoint_hashes": {
            key: canonical_hash(value) for key, value in endpoint_map.items()
        },
        "candidate": dict(candidate),
        "rollback": dict(rollback),
        "evaluator": dict(evaluator),
        "snapshots": dict(snapshots),
        "canary": dict(canary),
        "artifact_hashes": dict(artifact_hashes),
        "negative_probe": dict(negative_probe),
        "topology": dict(topology or {}),
        "policy": dict(policy or {}),
        "corpus": dict(corpus or {}),
        "evidence_root": evidence_root,
        "guard_path": guard_path,
    }
    return validate_execution_contract(result)


def _assert_not_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ContractError(f"path must not be a symlink: {path}")
    cursor = path.parent
    while cursor != cursor.parent:
        if cursor.is_symlink():
            raise ContractError(f"parent path must not be a symlink: {cursor}")
        cursor = cursor.parent


def _fsync_parent(path: Path) -> None:
    flags = getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path.parent, os.O_RDONLY | flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def create_evidence_root(base: Path | str, *, name: str | None = None) -> Path:
    """Create a fresh trusted mode-0700 evidence directory beneath ``base``."""

    parent = Path(base)
    if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
        raise ContractError("evidence parent must be a real directory")
    parent.mkdir(parents=True, exist_ok=True, mode=EVIDENCE_ROOT_MODE)
    os.chmod(parent, EVIDENCE_ROOT_MODE)
    chosen = name or f"argus-acceptance-v3-{os.getpid()}"
    if not _SAFE_NAME.fullmatch(chosen):
        raise ContractError("evidence root name is unsafe")
    root = parent / chosen
    _assert_not_symlink(root)
    try:
        root.mkdir(mode=EVIDENCE_ROOT_MODE)
    except FileExistsError as exc:
        raise ContractError("evidence root already exists") from exc
    os.chmod(root, EVIDENCE_ROOT_MODE)
    _fsync_parent(root)
    return root


def write_immutable_json(path: Path | str, value: Any) -> None:
    """Write compact canonical JSON once, mode 0600, with file/parent fsync."""

    target = Path(path)
    _assert_not_symlink(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise ContractError("evidence parent must not be a symlink")
    payload = canonical_bytes(value)
    fd = None
    try:
        fd = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            EVIDENCE_FILE_MODE,
        )
        os.fchmod(fd, EVIDENCE_FILE_MODE)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise ContractError("short evidence write")
            view = view[written:]
        os.fsync(fd)
    except FileExistsError as exc:
        raise ContractError(f"evidence file exists: {target}") from exc
    finally:
        if fd is not None:
            os.close(fd)
    _fsync_parent(target)


def write_phase_marker(
    path: Path | str, *, phase: str, identity: Mapping[str, Any]
) -> dict[str, Any]:
    """Persist a one-shot phase marker before dispatching its request."""

    if not isinstance(phase, str) or not _SAFE_NAME.fullmatch(phase):
        raise ContractError("phase name is unsafe")
    marker = {"phase": phase, "identity": dict(identity)}
    write_immutable_json(path, marker)
    return marker


def write_execution_contract(path: Path | str, contract: Mapping[str, Any]) -> str:
    """Validate, persist, and return the execution-contract hash."""

    checked = validate_execution_contract(contract)
    write_immutable_json(path, checked)
    return canonical_hash(checked)


def create_global_guard(
    path: Path | str, contract: Mapping[str, Any]
) -> dict[str, Any]:
    """Create the v3 global O_EXCL guard after contract validation."""

    checked = validate_execution_contract(contract)
    guard = {
        "cycle_id": CYCLE_ID,
        "profile": PROFILE,
        "schema": SCHEMA,
        "execution_contract_sha256": canonical_hash(checked),
        "request_sha256": checked["request_sha256"],
        "start_body_sha256": checked["start_body_sha256"],
        "candidate": checked["candidate"],
        "rollback": checked["rollback"],
        "evaluator": checked["evaluator"],
        "canary": checked["canary"],
    }
    write_immutable_json(path, guard)
    return guard


def bind_returned_run(
    path: Path | str,
    *,
    run_id: str,
    kind: str,
    topic: str,
    request_sha256: str,
    body_sha256: str,
    dispatched_at: str,
) -> dict[str, Any]:
    """Bind one returned run ID without allowing a replacement or retry."""

    if not isinstance(run_id, str) or not run_id or len(run_id) > 200:
        raise ContractError("run_id is invalid")
    if not isinstance(kind, str) or not kind:
        raise ContractError("run kind is required")
    if not isinstance(topic, str) or not topic or len(topic) > 200:
        raise ContractError("topic is invalid")
    _sha(request_sha256, "request_sha256")
    _sha(body_sha256, "body_sha256")
    bound = {
        "run_id": run_id,
        "kind": kind,
        "topic": topic,
        "request_sha256": request_sha256,
        "body_sha256": body_sha256,
        "dispatched_at": _timestamp(dispatched_at, "dispatched_at"),
    }
    write_immutable_json(path, bound)
    return bound
