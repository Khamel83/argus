"""Typed, secret-free, atomically published scorecard evidence bundles."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any, Mapping

from .stability import HARD_GATES, REQUIRED_PROFILES, StabilityVerdict


class BundleError(ValueError):
    """A bundle is incomplete, inconsistent, or unsafe to publish."""


SCHEMA_VERSION = "scorecard-bundle-v2"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{7,64}")
_CHECKSUM_LINE = re.compile(r"([0-9a-f]{64})  ([^\n]+)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")
_FORBIDDEN_VALUES = (
    re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9+/_.=-]+", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]+\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]+\b"),
    re.compile(r"__[A-Z0-9_]*SECRET[A-Z0-9_]*__", re.IGNORECASE),
    _JWT,
)
_FORBIDDEN_KEYS = {
    "authorization",
    "auth_header",
    "authorization_header",
    "credential",
    "credentials",
    "password",
    "secret",
    "api_key",
    "provider_key",
    "raw_payload",
    "native_payload",
    "raw_response",
    "provider_response",
    "cookies",
}
_IDENTITY_FIELDS = {
    "generation",
    "commit",
    "image_digest",
    "sanitized_config_sha256",
    "dimensions",
    "started_at",
    "finished_at",
}
_DIMENSION_FIELDS = {
    "corpus_hashes",
    "evaluator",
    "topology",
    "profile",
    "provider_snapshot_sha256",
}


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode()
    except (TypeError, ValueError) as exc:
        raise BundleError(f"document is not canonical JSON: {exc}") from exc


def _hash_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def derive_generation(dimensions: Mapping[str, Any]) -> str:
    """Derive the immutable benchmark generation from synchronized dimensions."""
    validated = _validate_dimensions(dimensions)
    return _hash_bytes(_canonical_bytes(validated))


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise BundleError(f"{label} must contain exact schema keys")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BundleError(f"{label} must be an object")
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise BundleError(f"{label} must be a lowercase SHA-256")
    return value


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise BundleError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise BundleError(f"{label} must include a timezone")
    return value


def _scan_safe(value: Any, location: str = "document") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise BundleError(f"non-string field at {location}")
            normalized = key.lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS or normalized.endswith(
                ("_password", "_credential", "_secret", "_api_key", "_auth_header")
            ):
                raise BundleError(f"forbidden sensitive field at {location}.{key}")
            _scan_safe(nested, f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _scan_safe(nested, f"{location}[{index}]")
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in _FORBIDDEN_VALUES):
            raise BundleError(f"forbidden sensitive value at {location}")


def _canonical_relative(relative: object) -> str:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise BundleError("bundle path must be a canonical relative POSIX path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise BundleError("bundle path must remain beneath the bundle root")
    if pure.as_posix() != relative:
        raise BundleError("bundle path is noncanonical")
    return relative


def _safe_path(root: Path, relative: object, *, require_file: bool = False) -> Path:
    canonical = _canonical_relative(relative)
    if root.is_symlink():
        raise BundleError("bundle root must not be a symlink")
    path = root.joinpath(*PurePosixPath(canonical).parts)
    cursor = root
    for part in PurePosixPath(canonical).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BundleError(f"bundle path contains symlink: {canonical}")
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise BundleError("bundle path escapes the bundle root") from exc
    if require_file and not path.is_file():
        raise BundleError(f"bundle missing declared file: {canonical}")
    return path


def _validate_dimensions(value: object) -> dict[str, Any]:
    dimensions = _mapping(value, "identity dimensions")
    _exact_keys(dimensions, _DIMENSION_FIELDS, "identity dimensions")
    corpus_hashes = _mapping(dimensions["corpus_hashes"], "corpus hashes")
    if not corpus_hashes:
        raise BundleError("identity dimensions require corpus hashes")
    checked_hashes = {
        _canonical_relative(path): _sha256(digest, f"corpus hash {path}")
        for path, digest in corpus_hashes.items()
    }
    evaluator = _mapping(dimensions["evaluator"], "evaluator identity")
    _exact_keys(
        evaluator, {"model", "prompt_sha256", "settings_sha256"}, "evaluator identity"
    )
    if not isinstance(evaluator["model"], str) or not evaluator["model"]:
        raise BundleError("evaluator model is required")
    _sha256(evaluator["prompt_sha256"], "evaluator prompt hash")
    _sha256(evaluator["settings_sha256"], "evaluator settings hash")
    topology = _mapping(dimensions["topology"], "topology identity")
    _exact_keys(topology, {"egress", "machine"}, "topology identity")
    if topology["egress"] not in {"hermetic", "residential", "datacenter"}:
        raise BundleError("invalid topology egress")
    if not isinstance(topology["machine"], str) or not topology["machine"]:
        raise BundleError("topology machine is required")
    if dimensions["profile"] not in {"hermetic", "free", "budgeted"}:
        raise BundleError("invalid scorecard profile")
    _sha256(dimensions["provider_snapshot_sha256"], "provider snapshot hash")
    return {
        "corpus_hashes": checked_hashes,
        "evaluator": dict(evaluator),
        "topology": dict(topology),
        "profile": dimensions["profile"],
        "provider_snapshot_sha256": dimensions["provider_snapshot_sha256"],
    }


def _validate_identity(value: object, label: str, generation: str) -> dict[str, Any]:
    identity = _mapping(value, f"{label} identity")
    _exact_keys(identity, _IDENTITY_FIELDS, f"{label} identity")
    if identity["generation"] != generation:
        raise BundleError(f"{label} identity generation mismatch")
    if not isinstance(identity["commit"], str) or not _COMMIT.fullmatch(
        identity["commit"]
    ):
        raise BundleError(f"{label} identity requires an immutable commit")
    if not isinstance(identity["image_digest"], str) or not _IMAGE_DIGEST.fullmatch(
        identity["image_digest"]
    ):
        raise BundleError(f"{label} identity requires an immutable image digest")
    _sha256(identity["sanitized_config_sha256"], "sanitized configuration hash")
    dimensions = _validate_dimensions(identity["dimensions"])
    if derive_generation(dimensions) != generation:
        raise BundleError(f"{label} identity dimensions do not derive generation")
    started = _timestamp(identity["started_at"], f"{label} started_at")
    finished = _timestamp(identity["finished_at"], f"{label} finished_at")
    if datetime.fromisoformat(started.replace("Z", "+00:00")) > datetime.fromisoformat(
        finished.replace("Z", "+00:00")
    ):
        raise BundleError(f"{label} identity timestamps are reversed")
    return dict(identity)


def _validate_corpus(value: object, dimensions: Mapping[str, Any], generation: str):
    corpus = _mapping(value, "corpus manifest")
    expected = {
        "version",
        "hashes",
        "hermetic_search_case_ids",
        "hermetic_extraction_case_ids",
        "competitive_case_ids",
        "dimensions",
        "generation",
    }
    _exact_keys(corpus, expected, "corpus manifest")
    if not isinstance(corpus["version"], str) or not corpus["version"]:
        raise BundleError("corpus version is required")
    if corpus["generation"] != generation or corpus["dimensions"] != dimensions:
        raise BundleError("corpus identity is not synchronized with generation")
    if corpus["hashes"] != dimensions["corpus_hashes"]:
        raise BundleError("corpus hashes do not match identity dimensions")
    for field in (
        "hermetic_search_case_ids",
        "hermetic_extraction_case_ids",
        "competitive_case_ids",
    ):
        ids = corpus[field]
        if (
            not isinstance(ids, list)
            or not ids
            or len(ids) != len(set(ids))
            or any(not isinstance(case_id, str) or not case_id for case_id in ids)
        ):
            raise BundleError(f"corpus {field} must contain unique case ids")
    return dict(corpus)


def _validate_provider_snapshot(
    value: object, dimensions: Mapping[str, Any]
) -> dict[str, Any]:
    snapshot = _mapping(value, "provider snapshot")
    _exact_keys(snapshot, {"schema", "profile", "providers"}, "provider snapshot")
    if snapshot["schema"] != "normalized-provider-snapshot-v1":
        raise BundleError("unsupported provider snapshot schema")
    if snapshot["profile"] != dimensions["profile"]:
        raise BundleError("provider snapshot profile mismatch")
    if not isinstance(snapshot["providers"], list):
        raise BundleError("provider snapshot providers must be a list")
    if (
        _hash_bytes(_canonical_bytes(snapshot))
        != dimensions["provider_snapshot_sha256"]
    ):
        raise BundleError("provider snapshot hash mismatch")
    return dict(snapshot)


def _validate_surface(value: object) -> dict[str, Any]:
    surface = _mapping(value, "surface equivalence")
    _exact_keys(surface, {"schema", "status", "cases"}, "surface equivalence")
    if surface["schema"] != "surface-equivalence-v1" or surface["status"] not in {
        "pass",
        "fail",
    }:
        raise BundleError("surface equivalence has invalid schema or status")
    if not isinstance(surface["cases"], list) or not surface["cases"]:
        raise BundleError("surface equivalence requires cases")
    return dict(surface)


def _validate_receipts(
    timing: object, persistence: object, lane: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    timing = _mapping(timing, "timing receipts")
    _exact_keys(timing, {"schema", "operations"}, "timing receipts")
    if timing["schema"] != "normalized-timing-receipts-v1":
        raise BundleError("unsupported timing receipt schema")
    operations = timing["operations"]
    if not isinstance(operations, list) or not operations:
        raise BundleError("timing receipts require operations")
    for operation in operations:
        operation = _mapping(operation, "timing operation")
        _exact_keys(
            operation,
            {"operation_id", "wall_ms", "component_ms", "timeout_source", "cache_ms"},
            "timing operation",
        )
        for field in ("wall_ms", "component_ms", "cache_ms"):
            if (
                isinstance(operation[field], bool)
                or not isinstance(operation[field], int)
                or operation[field] < 0
            ):
                raise BundleError("timing counters must be nonnegative integers")
        if operation["wall_ms"] > 120_000:
            raise BundleError("timing receipt exceeds bounded completion")
    persistence = _mapping(persistence, "persistence receipts")
    _exact_keys(
        persistence, {"schema", "status", "reason", "receipts"}, "persistence receipts"
    )
    if persistence["schema"] != "normalized-persistence-receipts-v1":
        raise BundleError("unsupported persistence receipt schema")
    if lane == "hermetic":
        if (
            persistence["status"] != "not_applicable"
            or persistence["receipts"] != []
            or not isinstance(persistence["reason"], str)
            or not persistence["reason"]
        ):
            raise BundleError("hermetic persistence receipt must be not_applicable")
    elif persistence["status"] != "accepted" or not persistence["receipts"]:
        raise BundleError("competitive persistence receipts are required")
    return dict(timing), dict(persistence)


def _validate_artifact(
    relative: str, artifact: object, corpus: Mapping[str, Any]
) -> dict[str, Any]:
    artifact = _mapping(artifact, f"artifact {relative}")
    if relative == "hermetic-summary.json":
        _exact_keys(
            artifact,
            {
                "schema",
                "search_cases",
                "extraction_cases",
                "provider_execution",
                "stability_verdict",
            },
            "hermetic summary",
        )
        if artifact["schema"] != "hermetic-summary-v1":
            raise BundleError("unsupported hermetic summary schema")
    elif relative.startswith("searches/"):
        _exact_keys(
            artifact,
            {
                "schema",
                "case_id",
                "mode",
                "profile",
                "input",
                "actual",
                "expected",
                "matched",
            },
            "search artifact",
        )
        if (
            artifact["schema"] != "normalized-search-fixture-v1"
            or artifact["case_id"] not in corpus["hermetic_search_case_ids"]
            or artifact["matched"] is not True
        ):
            raise BundleError("invalid normalized search artifact")
    elif relative.startswith("extractions/"):
        _exact_keys(
            artifact,
            {"schema", "case_id", "profile", "input", "actual", "expected", "matched"},
            "extraction artifact",
        )
        if (
            artifact["schema"] != "normalized-extraction-fixture-v1"
            or artifact["case_id"] not in corpus["hermetic_extraction_case_ids"]
            or artifact["matched"] is not True
        ):
            raise BundleError("invalid normalized extraction artifact")
    else:
        raise BundleError("artifact path is outside the allowlisted artifact schema")
    return dict(artifact)


def _validate_stability_document(value: object, manifest_verdict: object) -> None:
    document = _mapping(value, "stability gates")
    _exact_keys(
        document,
        {"verdict", "profiles", "architecture_exceptions"},
        "stability gates",
    )
    if document["verdict"] not in {"stable", "unstable"}:
        raise BundleError("stability verdict is invalid")
    if document["verdict"] != manifest_verdict:
        raise BundleError("manifest and stability verdicts differ")
    exceptions = document["architecture_exceptions"]
    if not isinstance(exceptions, list) or any(
        not isinstance(exception, str) or not exception for exception in exceptions
    ):
        raise BundleError("architecture exception evidence is invalid")
    profiles = _mapping(document["profiles"], "stability profiles")
    if set(profiles) != set(REQUIRED_PROFILES):
        raise BundleError("stability document requires both profiles")
    for profile in REQUIRED_PROFILES:
        profile_document = _mapping(profiles[profile], f"stability profile {profile}")
        _exact_keys(profile_document, {"verdict", "gates"}, "stability profile")
        if profile_document["verdict"] not in {"stable", "unstable"}:
            raise BundleError("profile stability verdict is invalid")
        gates = _mapping(profile_document["gates"], "stability gates")
        if set(gates) != set(HARD_GATES):
            raise BundleError("stability profile does not contain every hard gate")
        for gate, raw_verdict in gates.items():
            gate_verdict = _mapping(raw_verdict, f"stability gate {gate}")
            _exact_keys(
                gate_verdict, {"status", "reason", "evidence"}, "stability gate"
            )
            if gate_verdict["status"] not in {
                "pass",
                "fail",
                "inconclusive",
                "missing",
            }:
                raise BundleError("stability gate status is invalid")
            if (
                not isinstance(gate_verdict["reason"], str)
                or not gate_verdict["reason"]
                or not isinstance(gate_verdict["evidence"], Mapping)
            ):
                raise BundleError("stability gate reason or evidence is invalid")


def _validate_competitive_documents(
    metrics_value: object,
    comparisons_value: object,
    verdict_value: object,
    competitive_case_ids: list[str],
    manifest_verdict: object,
) -> None:
    metrics = _mapping(metrics_value, "competitive deterministic metrics")
    _exact_keys(
        metrics,
        {
            "schema",
            "consistent_pairs",
            "decisive_pairs",
            "candidate_wins",
            "baseline_wins",
            "p_value",
        },
        "competitive deterministic metrics",
    )
    if metrics["schema"] != "competitive-deterministic-metrics-v1":
        raise BundleError("unsupported competitive metrics schema")
    for field in (
        "consistent_pairs",
        "decisive_pairs",
        "candidate_wins",
        "baseline_wins",
    ):
        if (
            isinstance(metrics[field], bool)
            or not isinstance(metrics[field], int)
            or metrics[field] < 0
            or metrics[field] > len(competitive_case_ids)
        ):
            raise BundleError("competitive metric count is invalid")
    if metrics["p_value"] is not None and (
        isinstance(metrics["p_value"], bool)
        or not isinstance(metrics["p_value"], (int, float))
        or not 0 <= metrics["p_value"] <= 1
    ):
        raise BundleError("competitive p-value is invalid")
    comparisons = _mapping(comparisons_value, "blinded comparisons")
    _exact_keys(comparisons, {"schema", "pairs"}, "blinded comparisons")
    if comparisons["schema"] != "blinded-comparisons-v1":
        raise BundleError("unsupported blinded comparison schema")
    pairs = comparisons["pairs"]
    if not isinstance(pairs, list) or len(pairs) != len(competitive_case_ids):
        raise BundleError("blinded comparison coverage is incomplete")
    seen: set[str] = set()
    for pair_value in pairs:
        pair = _mapping(pair_value, "blinded pair")
        _exact_keys(
            pair,
            {"pair_id", "mode", "forward", "reverse", "classification"},
            "blinded pair",
        )
        pair_id = pair["pair_id"]
        if (
            not isinstance(pair_id, str)
            or pair_id not in competitive_case_ids
            or pair_id in seen
        ):
            raise BundleError("blinded comparison ids are not closed")
        seen.add(pair_id)
        if any(
            not isinstance(pair[field], str)
            for field in ("mode", "forward", "reverse", "classification")
        ):
            raise BundleError("blinded comparison values must be typed strings")
    verdict = _mapping(verdict_value, "competitive verdict")
    _exact_keys(verdict, {"schema", "verdict", "reason"}, "competitive verdict")
    if (
        verdict["schema"] != "competitive-verdict-v1"
        or verdict["verdict"]
        not in {"competitive", "not_competitive", "inconclusive", "unstable"}
        or verdict["verdict"] != manifest_verdict
        or not isinstance(verdict["reason"], str)
        or not verdict["reason"]
    ):
        raise BundleError("competitive verdict document is invalid")


def _required_static_paths(lane: str) -> set[str]:
    base = {
        "manifest.json",
        "identities/candidate.json",
        "corpus/manifest.json",
        "stability/gates.json",
        "stability/surface-equivalence.json",
        "stability/timing-receipts.json",
        "stability/persistence-receipts.json",
        "provider-snapshot.json",
        "artifacts/hermetic-summary.json",
        "checksums.sha256",
    }
    if lane == "competitive":
        return base | {
            "identities/baseline.json",
            "competitive/deterministic-metrics.json",
            "competitive/blinded-comparisons.json",
            "competitive/verdict.json",
        }
    if lane != "hermetic":
        raise BundleError(f"unsupported scorecard lane: {lane}")
    return base


def _prepare_documents(
    lane: str, stability: StabilityVerdict, payload: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise BundleError("bundle payload must be an object")
    _scan_safe(payload, "payload")
    required_payload = {
        "run_id",
        "generation",
        "candidate_identity",
        "corpus",
        "provider_snapshot",
        "surface_equivalence",
        "timing_receipts",
        "persistence_receipts",
        "artifacts",
    }
    if lane == "competitive":
        required_payload |= {"baseline_identity", "competitive"}
    if set(payload) != required_payload:
        raise BundleError("bundle payload must contain exact schema keys")
    run_id = payload["run_id"]
    generation = payload["generation"]
    if not isinstance(run_id, str) or not run_id:
        raise BundleError("bundle run_id is required")
    _sha256(generation, "bundle generation")
    candidate_dimensions = _validate_dimensions(
        _mapping(payload["candidate_identity"], "candidate identity").get("dimensions")
    )
    if derive_generation(candidate_dimensions) != generation:
        raise BundleError("manifest generation is not derived from fixed dimensions")
    candidate = _validate_identity(
        payload["candidate_identity"], "candidate", generation
    )
    baseline = None
    if lane == "competitive":
        baseline = _validate_identity(
            payload["baseline_identity"], "baseline", generation
        )
        if baseline["dimensions"] != candidate["dimensions"]:
            raise BundleError("baseline and candidate dimensions are not synchronized")
    corpus = _validate_corpus(payload["corpus"], candidate_dimensions, generation)
    provider_snapshot = _validate_provider_snapshot(
        payload["provider_snapshot"], candidate_dimensions
    )
    surface = _validate_surface(payload["surface_equivalence"])
    timing, persistence = _validate_receipts(
        payload["timing_receipts"], payload["persistence_receipts"], lane
    )
    artifacts = _mapping(payload["artifacts"], "artifacts")
    if "hermetic-summary.json" not in artifacts:
        raise BundleError("bundle requires artifacts/hermetic-summary.json")
    validated_artifacts: dict[str, Any] = {}
    for relative, artifact in artifacts.items():
        relative = _canonical_relative(relative)
        validated_artifacts[relative] = _validate_artifact(relative, artifact, corpus)
    expected_artifacts = {
        "hermetic-summary.json",
        *(f"searches/{case_id}.json" for case_id in corpus["hermetic_search_case_ids"]),
        *(
            f"extractions/{case_id}.json"
            for case_id in corpus["hermetic_extraction_case_ids"]
        ),
    }
    if set(validated_artifacts) != expected_artifacts:
        raise BundleError("artifact coverage is not closed against the corpus")

    documents: dict[str, Any] = {
        "identities/candidate.json": candidate,
        "corpus/manifest.json": corpus,
        "stability/gates.json": asdict(stability),
        "stability/surface-equivalence.json": surface,
        "stability/timing-receipts.json": timing,
        "stability/persistence-receipts.json": persistence,
        "provider-snapshot.json": provider_snapshot,
        **{
            f"artifacts/{relative}": artifact
            for relative, artifact in validated_artifacts.items()
        },
    }
    if baseline is not None:
        documents["identities/baseline.json"] = baseline
        competitive = _mapping(payload["competitive"], "competitive evidence")
        _exact_keys(
            competitive,
            {"deterministic_metrics", "blinded_comparisons", "verdict"},
            "competitive evidence",
        )
        _validate_competitive_documents(
            competitive["deterministic_metrics"],
            competitive["blinded_comparisons"],
            competitive["verdict"],
            corpus["competitive_case_ids"],
            competitive["verdict"].get("verdict")
            if isinstance(competitive["verdict"], Mapping)
            else None,
        )
        documents.update(
            {
                "competitive/deterministic-metrics.json": competitive[
                    "deterministic_metrics"
                ],
                "competitive/blinded-comparisons.json": competitive[
                    "blinded_comparisons"
                ],
                "competitive/verdict.json": competitive["verdict"],
            }
        )
    for relative, document in documents.items():
        _canonical_relative(relative)
        _scan_safe(document, relative)

    encoded = {path: _canonical_bytes(document) for path, document in documents.items()}
    sections = {
        "identities": "required",
        "corpus": "required",
        "stability": "required",
        "artifacts": "required",
        "competitive": "required" if lane == "competitive" else "not_run",
    }
    static = _required_static_paths(lane)
    file_set = sorted(static | set(encoded))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "generation": generation,
        "lane": lane,
        "sections": sections,
        "dimensions": candidate_dimensions,
        "identities": {
            "candidate": {
                "path": "identities/candidate.json",
                "sha256": _hash_bytes(encoded["identities/candidate.json"]),
            }
        },
        "stability_verdict": stability.verdict,
        "competitive_verdict": (
            "not_run"
            if lane == "hermetic"
            else payload["competitive"]["verdict"]["verdict"]
        ),
        "file_hashes": {
            path: _hash_bytes(content) for path, content in encoded.items()
        },
        "file_set": file_set,
    }
    if baseline is not None:
        manifest["identities"]["baseline"] = {
            "path": "identities/baseline.json",
            "sha256": _hash_bytes(encoded["identities/baseline.json"]),
        }
    _scan_safe(manifest, "manifest.json")
    encoded["manifest.json"] = _canonical_bytes(manifest)
    checksum_paths = sorted(set(file_set) - {"checksums.sha256"})
    encoded["checksums.sha256"] = (
        "\n".join(f"{_hash_bytes(encoded[path])}  {path}" for path in checksum_paths)
        + "\n"
    ).encode()
    if set(encoded) != set(file_set):
        raise BundleError("prepared bundle file set is not closed")
    return encoded


def write_bundle(
    output: Path,
    *,
    lane: str,
    stability: StabilityVerdict,
    payload: Mapping[str, Any],
) -> Path:
    """Validate entirely in memory, stage privately, verify, then atomically publish."""
    documents = _prepare_documents(lane, stability, payload)
    if output.exists() or output.is_symlink():
        raise BundleError(f"bundle output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent)
    )
    try:
        for relative, content in documents.items():
            path = _safe_path(staging, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        verify_bundle(staging)
        if output.exists() or output.is_symlink():
            raise BundleError(f"bundle output already exists: {output}")
        staging.replace(output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output


def _filesystem_files(root: Path) -> set[str]:
    if not root.is_dir() or root.is_symlink():
        raise BundleError("bundle root must be a real directory")
    files: set[str] = set()
    for directory, directories, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in directories:
            path = directory_path / name
            if path.is_symlink():
                raise BundleError(f"bundle contains symlink: {path.relative_to(root)}")
        for name in filenames:
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise BundleError(f"bundle contains symlink: {relative}")
            _canonical_relative(relative)
            files.add(relative)
    return files


def verify_bundle(root: Path) -> dict[str, Any]:
    """Validate the exact filesystem, manifest, identity, schema, and checksum set."""
    filesystem = _filesystem_files(root)
    manifest_path = _safe_path(root, "manifest.json", require_file=True)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError("bundle manifest is invalid JSON") from exc
    _scan_safe(manifest, "manifest.json")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise BundleError("unsupported bundle schema")
    _exact_keys(
        manifest,
        {
            "schema_version",
            "run_id",
            "generation",
            "lane",
            "sections",
            "dimensions",
            "identities",
            "stability_verdict",
            "competitive_verdict",
            "file_hashes",
            "file_set",
        },
        "bundle manifest",
    )
    lane = manifest.get("lane")
    sections = _mapping(manifest["sections"], "manifest sections")
    _exact_keys(
        sections,
        {"identities", "corpus", "stability", "artifacts", "competitive"},
        "manifest sections",
    )
    expected_sections = {
        "identities": "required",
        "corpus": "required",
        "stability": "required",
        "artifacts": "required",
        "competitive": "required" if lane == "competitive" else "not_run",
    }
    if sections != expected_sections:
        raise BundleError("manifest sections do not match the declared lane")
    required = _required_static_paths(lane)
    declared = manifest.get("file_set")
    if (
        not isinstance(declared, list)
        or len(declared) != len(set(declared))
        or any(_canonical_relative(path) != path for path in declared)
    ):
        raise BundleError("manifest file_set is invalid")
    declared_set = set(declared)
    if not required <= declared_set or declared_set != filesystem:
        raise BundleError("bundle has undeclared files or an unclosed file set")

    checksum_path = _safe_path(root, "checksums.sha256", require_file=True)
    checksum_lines = checksum_path.read_text(encoding="utf-8").splitlines()
    checksums: dict[str, str] = {}
    previous = ""
    for line in checksum_lines:
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise BundleError("malformed or noncanonical checksum entry")
        digest, relative = match.groups()
        relative = _canonical_relative(relative)
        if relative in checksums:
            raise BundleError(f"duplicate checksum entry: {relative}")
        if previous and relative < previous:
            raise BundleError("checksum entries must use canonical sort order")
        previous = relative
        checksums[relative] = digest
    if set(checksums) != declared_set - {"checksums.sha256"}:
        raise BundleError("checksum set is not closed against manifest file_set")
    for relative, expected in checksums.items():
        path = _safe_path(root, relative, require_file=True)
        if _hash_bytes(path.read_bytes()) != expected:
            raise BundleError(f"checksum mismatch: {relative}")

    file_hashes = manifest.get("file_hashes")
    if not isinstance(file_hashes, Mapping) or set(file_hashes) != declared_set - {
        "manifest.json",
        "checksums.sha256",
    }:
        raise BundleError("manifest file hashes are not closed")
    for relative, expected in file_hashes.items():
        if (
            not isinstance(expected, str)
            or not _SHA256.fullmatch(expected)
            or _hash_bytes(_safe_path(root, relative, require_file=True).read_bytes())
            != expected
        ):
            raise BundleError(f"manifest checksum mismatch: {relative}")

    dimensions = _validate_dimensions(manifest.get("dimensions"))
    generation = manifest.get("generation")
    if derive_generation(dimensions) != generation:
        raise BundleError("manifest generation does not match fixed dimensions")
    identities = _mapping(manifest.get("identities"), "manifest identities")
    expected_labels = (
        {"candidate", "baseline"} if lane == "competitive" else {"candidate"}
    )
    if set(identities) != expected_labels:
        raise BundleError("manifest identities are incomplete")
    for label, declaration_value in identities.items():
        declaration = _mapping(declaration_value, "identity declaration")
        _exact_keys(declaration, {"path", "sha256"}, "identity declaration")
        path = _safe_path(root, declaration["path"], require_file=True)
        if _hash_bytes(path.read_bytes()) != declaration["sha256"]:
            raise BundleError(f"identity checksum mismatch: {label}")
        try:
            identity = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BundleError(f"invalid identity document: {label}") from exc
        validated = _validate_identity(identity, label, generation)
        if validated["dimensions"] != dimensions:
            raise BundleError("cross-generation identity comparison")

    def load_json(relative: str) -> Any:
        try:
            return json.loads(
                _safe_path(root, relative, require_file=True).read_text(
                    encoding="utf-8"
                )
            )
        except json.JSONDecodeError as exc:
            raise BundleError(f"invalid JSON document: {relative}") from exc

    corpus = _validate_corpus(load_json("corpus/manifest.json"), dimensions, generation)
    _validate_provider_snapshot(load_json("provider-snapshot.json"), dimensions)
    _validate_surface(load_json("stability/surface-equivalence.json"))
    _validate_receipts(
        load_json("stability/timing-receipts.json"),
        load_json("stability/persistence-receipts.json"),
        lane,
    )
    _validate_stability_document(
        load_json("stability/gates.json"), manifest["stability_verdict"]
    )
    artifact_documents = {
        relative.removeprefix("artifacts/"): load_json(relative)
        for relative in declared_set
        if relative.startswith("artifacts/") and relative.endswith(".json")
    }
    expected_artifacts = {
        "hermetic-summary.json",
        *(f"searches/{case_id}.json" for case_id in corpus["hermetic_search_case_ids"]),
        *(
            f"extractions/{case_id}.json"
            for case_id in corpus["hermetic_extraction_case_ids"]
        ),
    }
    if set(artifact_documents) != expected_artifacts:
        raise BundleError("verified artifact coverage is not closed against corpus")
    for relative, document in artifact_documents.items():
        _validate_artifact(relative, document, corpus)
    if lane == "competitive":
        _validate_competitive_documents(
            load_json("competitive/deterministic-metrics.json"),
            load_json("competitive/blinded-comparisons.json"),
            load_json("competitive/verdict.json"),
            corpus["competitive_case_ids"],
            manifest["competitive_verdict"],
        )

    for relative in sorted(declared_set):
        if relative.endswith(".json"):
            document = load_json(relative)
            _scan_safe(document, relative)
    return manifest
