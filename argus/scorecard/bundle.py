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

from argus.contracts import CanonicalOutcome

from .competitive import CompetitiveInputError, classify_pair, evaluate_competitive
from .corpus import (
    COMPETITIVE_CASE_MODES,
    HERMETIC_EXTRACTION_CASE_IDS,
    HERMETIC_SEARCH_CASE_IDS,
)
from .stability import (
    HARD_GATES,
    REQUIRED_PROFILES,
    StabilityVerdict,
    evaluate_stability,
)


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
    "sanitized_config_sha256",
}
_NORMALIZED_OUTCOMES = frozenset(outcome.value for outcome in CanonicalOutcome)
_SUCCESS_LIKE_OUTCOMES = {
    CanonicalOutcome.SUCCESS.value,
    CanonicalOutcome.DEGRADED.value,
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
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            forbidden = {re.sub(r"[^a-z0-9]", "", item) for item in _FORBIDDEN_KEYS}
            if normalized in forbidden or normalized.endswith(
                ("password", "credential", "secret", "apikey", "authheader")
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
        evaluator,
        {
            "status",
            "model",
            "prompt_sha256",
            "settings_sha256",
            "reason_code",
        },
        "evaluator identity",
    )
    if evaluator["status"] == "pinned":
        if not isinstance(evaluator["model"], str) or not evaluator["model"]:
            raise BundleError("pinned evaluator model is required")
        if evaluator["reason_code"] is not None:
            raise BundleError("pinned evaluator cannot have a reason code")
    elif evaluator["status"] == "unavailable":
        if evaluator["model"] is not None:
            raise BundleError("unavailable evaluator cannot name a model")
        if (
            not isinstance(evaluator["reason_code"], str)
            or not evaluator["reason_code"]
        ):
            raise BundleError("unavailable evaluator reason code is required")
    else:
        raise BundleError("evaluator status must be pinned or unavailable")
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
    _sha256(dimensions["sanitized_config_sha256"], "dimension configuration hash")
    return {
        "corpus_hashes": checked_hashes,
        "evaluator": dict(evaluator),
        "topology": dict(topology),
        "profile": dimensions["profile"],
        "provider_snapshot_sha256": dimensions["provider_snapshot_sha256"],
        "sanitized_config_sha256": dimensions["sanitized_config_sha256"],
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
    dimensions = _validate_dimensions(identity["dimensions"])
    image_digest = identity["image_digest"]
    if dimensions["profile"] == "hermetic":
        if image_digest is not None:
            raise BundleError(f"{label} hermetic identity must omit image digest")
    elif not isinstance(image_digest, str) or not _IMAGE_DIGEST.fullmatch(image_digest):
        raise BundleError(f"{label} identity requires an immutable image digest")
    _sha256(identity["sanitized_config_sha256"], "sanitized configuration hash")
    if identity["sanitized_config_sha256"] != dimensions["sanitized_config_sha256"]:
        raise BundleError(f"{label} configuration identity is not generation-bound")
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
    if corpus["version"] == "scorecard-v1":
        expected_sets = {
            "hermetic_search_case_ids": set(HERMETIC_SEARCH_CASE_IDS),
            "hermetic_extraction_case_ids": set(HERMETIC_EXTRACTION_CASE_IDS),
            "competitive_case_ids": set(COMPETITIVE_CASE_MODES),
        }
        for field, expected_ids in expected_sets.items():
            if set(corpus[field]) != expected_ids:
                raise BundleError(f"corpus {field} does not match canonical set")
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
    for provider_value in snapshot["providers"]:
        provider = _mapping(provider_value, "provider snapshot entry")
        _exact_keys(
            provider,
            {"provider", "tier", "fixture_contract_version", "status"},
            "provider snapshot entry",
        )
        if (
            not isinstance(provider["provider"], str)
            or not provider["provider"]
            or isinstance(provider["tier"], bool)
            or not isinstance(provider["tier"], int)
            or provider["tier"] not in {0, 1, 3}
            or not isinstance(provider["fixture_contract_version"], str)
            or not provider["fixture_contract_version"]
            or provider["status"] not in {"fixture_verified", "ready", "unready"}
        ):
            raise BundleError("provider snapshot entry is invalid")
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
    for case_value in surface["cases"]:
        case = _mapping(case_value, "surface case")
        _exact_keys(
            case,
            {
                "case_id",
                "outcome",
                "http_status",
                "mcp_is_error",
                "cli_exit",
                "python_error",
            },
            "surface case",
        )
        if (
            not isinstance(case["case_id"], str)
            or not case["case_id"]
            or not isinstance(case["outcome"], str)
            or isinstance(case["http_status"], bool)
            or not isinstance(case["http_status"], int)
            or isinstance(case["cli_exit"], bool)
            or not isinstance(case["cli_exit"], int)
            or not isinstance(case["mcp_is_error"], bool)
            or not isinstance(case["python_error"], bool)
        ):
            raise BundleError("surface case values are invalid")
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
        if (
            not isinstance(operation["operation_id"], str)
            or not operation["operation_id"]
            or operation["timeout_source"]
            not in {
                "none",
                "provider",
                "extraction",
                "operation",
            }
            or operation["component_ms"] > operation["wall_ms"]
            or operation["cache_ms"] > operation["wall_ms"]
        ):
            raise BundleError("timing operation is inconsistent")
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
    if lane != "hermetic":
        for receipt_value in persistence["receipts"]:
            receipt = _mapping(receipt_value, "persistence receipt")
            _exact_keys(
                receipt,
                {"operation_id", "repository", "durable_id", "status"},
                "persistence receipt",
            )
            if (
                not all(
                    isinstance(receipt[field], str) and receipt[field]
                    for field in receipt
                )
                or receipt["status"] != "accepted"
            ):
                raise BundleError("persistence receipt is invalid")
    return dict(timing), dict(persistence)


def _validate_normalized_search_evidence(value: object, label: str) -> None:
    evidence = _mapping(value, label)
    _exact_keys(evidence, {"outcome", "results", "diagnostics"}, label)
    outcome = evidence["outcome"]
    results = evidence["results"]
    if outcome not in _NORMALIZED_OUTCOMES or not isinstance(results, list):
        raise BundleError(f"{label} is invalid")
    if outcome in _SUCCESS_LIKE_OUTCOMES and not results:
        raise BundleError(f"{label} success-like outcome requires results")
    if outcome not in _SUCCESS_LIKE_OUTCOMES and results:
        raise BundleError(f"{label} non-success outcome forbids results")
    for result_value in results:
        result = _mapping(result_value, f"{label} result")
        _exact_keys(
            result,
            {
                "url",
                "title",
                "snippet",
                "domain",
                "provider",
                "score",
                "egress",
                "machine",
            },
            f"{label} result",
        )
        if (
            any(
                not isinstance(result[field], str) or not result[field]
                for field in (
                    "url",
                    "title",
                    "domain",
                    "provider",
                    "egress",
                    "machine",
                )
            )
            or not isinstance(result["snippet"], str)
            or isinstance(result["score"], bool)
            or not isinstance(result["score"], (int, float))
        ):
            raise BundleError(f"{label} result is invalid")
    _validate_live_diagnostics(evidence["diagnostics"], f"{label} diagnostics")


def _validate_normalized_extraction_evidence(value: object, label: str) -> None:
    evidence = _mapping(value, label)
    _exact_keys(evidence, {"outcome", "content", "diagnostics"}, label)
    outcome = evidence["outcome"]
    if outcome not in _NORMALIZED_OUTCOMES:
        raise BundleError(f"{label} is invalid")
    if evidence["content"] is None:
        if outcome in _SUCCESS_LIKE_OUTCOMES:
            raise BundleError(f"{label} success-like outcome requires content")
        _validate_live_diagnostics(evidence["diagnostics"], f"{label} diagnostics")
        return
    if outcome not in _SUCCESS_LIKE_OUTCOMES:
        raise BundleError(f"{label} non-success outcome forbids content")
    content = _mapping(evidence["content"], f"{label} content")
    _exact_keys(
        content,
        {
            "url",
            "title",
            "text",
            "author",
            "date",
            "word_count",
            "egress",
            "machine",
            "source_type",
        },
        f"{label} content",
    )
    if (
        any(
            not isinstance(content[field], str) or not content[field]
            for field in (
                "url",
                "title",
                "text",
                "egress",
                "machine",
                "source_type",
            )
        )
        or any(
            content[field] is not None and not isinstance(content[field], str)
            for field in ("author", "date")
        )
        or isinstance(content["word_count"], bool)
        or not isinstance(content["word_count"], int)
        or content["word_count"] < 0
    ):
        raise BundleError(f"{label} content is invalid")
    _validate_live_diagnostics(evidence["diagnostics"], f"{label} diagnostics")


def _validate_live_diagnostics(value: object, label: str) -> None:
    diagnostics = _mapping(value, label)
    _exact_keys(
        diagnostics,
        {"timing", "attempts", "spend", "cache", "freshness", "persistence"},
        label,
    )
    timing = _mapping(diagnostics["timing"], f"{label} timing")
    _exact_keys(
        timing,
        {"operation_id", "wall_ms", "component_ms", "timeout_source", "cache_ms"},
        f"{label} timing",
    )
    for field in ("wall_ms", "component_ms", "cache_ms"):
        if (
            isinstance(timing[field], bool)
            or not isinstance(timing[field], int)
            or timing[field] < 0
        ):
            raise BundleError(f"{label} timing is invalid")
    if (
        not isinstance(timing["operation_id"], str)
        or not timing["operation_id"]
        or timing["wall_ms"] > 120_000
        or timing["component_ms"] > timing["wall_ms"]
        or timing["cache_ms"] > timing["wall_ms"]
        or timing["timeout_source"]
        not in {"none", "provider", "extraction", "operation"}
    ):
        raise BundleError(f"{label} timing is inconsistent")
    attempts = diagnostics["attempts"]
    if not isinstance(attempts, list) or not attempts:
        raise BundleError(f"{label} attempts are required")
    for raw_attempt in attempts:
        attempt = _mapping(raw_attempt, f"{label} attempt")
        _exact_keys(
            attempt,
            {
                "name",
                "kind",
                "tier",
                "status",
                "reason",
                "result_count",
                "latency_ms",
            },
            f"{label} attempt",
        )
        if (
            not isinstance(attempt["name"], str)
            or not attempt["name"]
            or attempt["kind"] not in {"provider", "extractor"}
            or (
                attempt["kind"] == "provider"
                and (
                    isinstance(attempt["tier"], bool)
                    or not isinstance(attempt["tier"], int)
                    or attempt["tier"] != 0
                )
            )
            or (attempt["kind"] == "extractor" and attempt["tier"] is not None)
            or not isinstance(attempt["status"], str)
            or not attempt["status"]
            or not isinstance(attempt["reason"], str)
            or not attempt["reason"]
            or any(
                isinstance(attempt[field], bool)
                or not isinstance(attempt[field], int)
                or attempt[field] < 0
                for field in ("result_count", "latency_ms")
            )
        ):
            raise BundleError(f"{label} attempt is invalid")
    spend = _mapping(diagnostics["spend"], f"{label} spend")
    _exact_keys(
        spend,
        {
            "provider_calls",
            "reserved_usd",
            "actual_usd",
            "accounting_source",
            "reconciliation",
        },
        f"{label} spend",
    )
    if (
        isinstance(spend["provider_calls"], bool)
        or not isinstance(spend["provider_calls"], int)
        or spend["provider_calls"] < 0
        or spend["reserved_usd"] != 0
        or spend["actual_usd"] != 0
        or not isinstance(spend["accounting_source"], str)
        or not spend["accounting_source"]
        or spend["reconciliation"] != "settled"
    ):
        raise BundleError(f"{label} must prove zero spend")
    cache = _mapping(diagnostics["cache"], f"{label} cache")
    _exact_keys(
        cache,
        {"status", "age_ms", "origin", "origin_spend_usd", "eligible"},
        f"{label} cache",
    )
    if (
        cache["status"] not in {"hit", "miss", "bypass"}
        or isinstance(cache["age_ms"], bool)
        or not isinstance(cache["age_ms"], int)
        or cache["age_ms"] < 0
        or not isinstance(cache["origin"], str)
        or not cache["origin"]
        or cache["origin_spend_usd"] != 0
        or not isinstance(cache["eligible"], bool)
    ):
        raise BundleError(f"{label} cache evidence is invalid")
    freshness = _mapping(diagnostics["freshness"], f"{label} freshness")
    _exact_keys(
        freshness,
        {
            "observed_at",
            "age_seconds",
            "window_seconds",
            "status",
            "reason",
        },
        f"{label} freshness",
    )
    _timestamp(freshness["observed_at"], f"{label} freshness observed_at")
    if (
        any(
            isinstance(freshness[field], bool)
            or not isinstance(freshness[field], int)
            or freshness[field] < 0
            for field in ("age_seconds", "window_seconds")
        )
        or freshness["status"] != "fresh"
        or freshness["age_seconds"] > freshness["window_seconds"]
        or not isinstance(freshness["reason"], str)
        or not freshness["reason"]
    ):
        raise BundleError(f"{label} freshness evidence is invalid")
    persistence = _mapping(diagnostics["persistence"], f"{label} persistence")
    _exact_keys(
        persistence,
        {"repository", "durable_id", "status"},
        f"{label} persistence",
    )
    if (
        persistence["repository"] != "postgresql"
        or not isinstance(persistence["durable_id"], str)
        or not persistence["durable_id"]
        or persistence["status"] != "accepted"
    ):
        raise BundleError(f"{label} persistence receipt is invalid")


def _validate_artifact(
    relative: str, artifact: object, corpus: Mapping[str, Any], lane: str
) -> dict[str, Any]:
    artifact = _mapping(artifact, f"artifact {relative}")
    if lane == "hermetic" and relative == "hermetic-summary.json":
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
    elif lane == "competitive" and relative.startswith("searches/"):
        _exact_keys(
            artifact,
            {"schema", "case_id", "mode", "request", "baseline", "candidate"},
            "competitive search artifact",
        )
        case_id = artifact["case_id"]
        if (
            artifact["schema"] != "normalized-competitive-search-v1"
            or case_id not in corpus["competitive_case_ids"]
            or COMPETITIVE_CASE_MODES.get(case_id) != artifact["mode"]
            or artifact["mode"] == "extraction"
        ):
            raise BundleError("invalid normalized competitive search artifact")
        request = _mapping(artifact["request"], "competitive search request")
        _exact_keys(
            request,
            {"query", "free_only", "providers", "caller"},
            "competitive search request",
        )
        if (
            not isinstance(request["query"], str)
            or not request["query"]
            or request["free_only"] is not True
            or not isinstance(request["providers"], list)
            or not request["providers"]
            or any(
                not isinstance(provider, str) or not provider
                for provider in request["providers"]
            )
            or not isinstance(request["caller"], str)
            or not request["caller"]
        ):
            raise BundleError("competitive search request is invalid")
        _validate_normalized_search_evidence(
            artifact["baseline"], "competitive baseline search evidence"
        )
        _validate_normalized_search_evidence(
            artifact["candidate"], "competitive candidate search evidence"
        )
    elif lane == "competitive" and relative.startswith("extractions/"):
        _exact_keys(
            artifact,
            {
                "schema",
                "case_id",
                "mode",
                "request",
                "capture",
                "baseline",
                "candidate",
            },
            "competitive extraction artifact",
        )
        case_id = artifact["case_id"]
        if (
            artifact["schema"] != "normalized-competitive-extraction-v1"
            or case_id not in corpus["competitive_case_ids"]
            or COMPETITIVE_CASE_MODES.get(case_id) != "extraction"
            or artifact["mode"] != "extraction"
        ):
            raise BundleError("invalid normalized competitive extraction artifact")
        request = _mapping(artifact["request"], "competitive extraction request")
        capture = _mapping(artifact["capture"], "competitive extraction capture")
        expected_capture_keys = {
            "case_id",
            "snapshot_id",
            "url",
            "url_sha256",
            "capture_sha256",
        }
        _exact_keys(
            request,
            {"url", "snapshot_id", "url_sha256", "caller"},
            "competitive extraction request",
        )
        _exact_keys(capture, expected_capture_keys, "competitive extraction capture")
        if (
            capture["case_id"] != case_id
            or any(
                not isinstance(capture[field], str) or not capture[field]
                for field in ("snapshot_id", "url")
            )
            or _sha256(capture["url_sha256"], "capture URL hash")
            != _hash_bytes(capture["url"].encode())
            or _sha256(capture["capture_sha256"], "capture content hash")
            != capture["capture_sha256"]
            or request
            != {
                "url": capture["url"],
                "snapshot_id": capture["snapshot_id"],
                "url_sha256": capture["url_sha256"],
                "caller": request.get("caller"),
            }
            or not isinstance(request["caller"], str)
            or not request["caller"]
        ):
            raise BundleError("competitive extraction snapshot is inconsistent")
        _validate_normalized_extraction_evidence(
            artifact["baseline"], "competitive baseline extraction evidence"
        )
        _validate_normalized_extraction_evidence(
            artifact["candidate"], "competitive candidate extraction evidence"
        )
    elif lane == "hermetic" and relative.startswith("searches/"):
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
        raw_input = _mapping(artifact["input"], "search artifact input")
        _exact_keys(raw_input, {"query", "raw_fixture_id"}, "search artifact input")
        for label in ("actual", "expected"):
            evidence = _mapping(artifact[label], f"search artifact {label}")
            _exact_keys(
                evidence,
                {"outcome", "result_count", "domain_count", "provenance_complete"},
                f"search artifact {label}",
            )
            if (
                not isinstance(evidence["outcome"], str)
                or any(
                    isinstance(evidence[field], bool)
                    or not isinstance(evidence[field], int)
                    or evidence[field] < 0
                    for field in ("result_count", "domain_count")
                )
                or not isinstance(evidence["provenance_complete"], bool)
            ):
                raise BundleError("search artifact evidence is invalid")
        if artifact["actual"] != artifact["expected"]:
            raise BundleError("search artifact does not match expected evidence")
    elif lane == "hermetic" and relative.startswith("extractions/"):
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
        raw_input = _mapping(artifact["input"], "extraction artifact input")
        _exact_keys(
            raw_input, {"raw_fixture_id", "content_type"}, "extraction artifact input"
        )
        for label in ("actual", "expected"):
            evidence = _mapping(artifact[label], f"extraction artifact {label}")
            _exact_keys(
                evidence,
                {"outcome", "quality", "complete", "provenance_complete"},
                f"extraction artifact {label}",
            )
            if (
                not isinstance(evidence["outcome"], str)
                or evidence["quality"] not in {"passing", "degraded", "failed"}
                or not isinstance(evidence["complete"], bool)
                or not isinstance(evidence["provenance_complete"], bool)
            ):
                raise BundleError("extraction artifact evidence is invalid")
        if artifact["actual"] != artifact["expected"]:
            raise BundleError("extraction artifact does not match expected evidence")
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
    if not isinstance(exceptions, (list, tuple)) or any(
        not isinstance(exception, str) or not exception for exception in exceptions
    ):
        raise BundleError("architecture exception evidence is invalid")
    profiles = _mapping(document["profiles"], "stability profiles")
    if set(profiles) != set(REQUIRED_PROFILES):
        raise BundleError("stability document requires both profiles")
    reconstructed: dict[str, dict[str, Any]] = {}
    for profile in REQUIRED_PROFILES:
        reconstructed[profile] = {}
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
            evidence = _mapping(gate_verdict["evidence"], "stability gate evidence")
            _exact_keys(
                evidence,
                {"schema", "fixture_id", "check"},
                "stability gate evidence",
            )
            check = _mapping(evidence["check"], "stability gate check")
            _exact_keys(
                check,
                {"kind", "passed", "observation_count"},
                "stability gate check",
            )
            if (
                evidence["schema"] != "normalized-gate-evidence-v2"
                or not isinstance(evidence["fixture_id"], str)
                or not evidence["fixture_id"]
                or check["kind"] != gate
                or not isinstance(check["passed"], bool)
                or isinstance(check["observation_count"], bool)
                or not isinstance(check["observation_count"], int)
                or check["observation_count"] <= 0
                or check["passed"] != (gate_verdict["status"] == "pass")
            ):
                raise BundleError("stability gate evidence is inconsistent")
            reconstructed[profile][gate] = dict(gate_verdict)
    recomputed = evaluate_stability(
        reconstructed,
        architecture_exceptions=tuple(exceptions),
    )
    recomputed_document = asdict(recomputed)
    if isinstance(exceptions, list):
        recomputed_document["architecture_exceptions"] = list(
            recomputed_document["architecture_exceptions"]
        )
    if recomputed_document != document:
        raise BundleError("stability verdict is not derivable from serialized gates")


def _validate_competitive_documents(
    metrics_value: object,
    comparisons_value: object,
    verdict_value: object,
    competitive_case_ids: list[str],
    manifest_verdict: object,
    stability: StabilityVerdict,
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
    raw_pairs: list[dict[str, str]] = []
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
        raw_pair = {
            "pair_id": pair["pair_id"],
            "mode": pair["mode"],
            "forward": pair["forward"],
            "reverse": pair["reverse"],
        }
        if classify_pair(raw_pair).classification != pair["classification"]:
            raise BundleError("serialized pair classification is not derivable")
        raw_pairs.append(raw_pair)
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
    try:
        computed = evaluate_competitive(stability, raw_pairs)
    except CompetitiveInputError as exc:
        raise BundleError(str(exc)) from exc
    expected_metrics = {
        "schema": "competitive-deterministic-metrics-v1",
        "consistent_pairs": computed.consistent_pairs,
        "decisive_pairs": computed.candidate_wins + computed.baseline_wins,
        "candidate_wins": computed.candidate_wins,
        "baseline_wins": computed.baseline_wins,
        "p_value": computed.p_value,
    }
    if metrics != expected_metrics:
        raise BundleError("competitive metrics are not derivable from raw pairs")
    if verdict != {
        "schema": "competitive-verdict-v1",
        "verdict": computed.verdict,
        "reason": computed.reason,
    }:
        raise BundleError("competitive verdict is not derivable from raw pairs")


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
    return base | {"artifacts/hermetic-summary.json"}


def _expected_artifact_paths(corpus: Mapping[str, Any], lane: str) -> set[str]:
    if lane == "hermetic":
        return {
            "hermetic-summary.json",
            *(
                f"searches/{case_id}.json"
                for case_id in corpus["hermetic_search_case_ids"]
            ),
            *(
                f"extractions/{case_id}.json"
                for case_id in corpus["hermetic_extraction_case_ids"]
            ),
        }
    return {
        *(
            f"searches/{case_id}.json"
            for case_id in corpus["competitive_case_ids"]
            if COMPETITIVE_CASE_MODES.get(case_id) != "extraction"
        ),
        *(
            f"extractions/{case_id}.json"
            for case_id in corpus["competitive_case_ids"]
            if COMPETITIVE_CASE_MODES.get(case_id) == "extraction"
        ),
    }


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
        baseline_started = datetime.fromisoformat(
            baseline["started_at"].replace("Z", "+00:00")
        )
        candidate_started = datetime.fromisoformat(
            candidate["started_at"].replace("Z", "+00:00")
        )
        synchronized_finish = max(
            datetime.fromisoformat(baseline["finished_at"].replace("Z", "+00:00")),
            datetime.fromisoformat(candidate["finished_at"].replace("Z", "+00:00")),
        )
        synchronized_start = min(baseline_started, candidate_started)
        if (
            abs((baseline_started - candidate_started).total_seconds()) > 900
            or (synchronized_finish - synchronized_start).total_seconds() > 900
        ):
            raise BundleError("baseline and candidate exceeded synchronization window")
    corpus = _validate_corpus(payload["corpus"], candidate_dimensions, generation)
    provider_snapshot = _validate_provider_snapshot(
        payload["provider_snapshot"], candidate_dimensions
    )
    surface = _validate_surface(payload["surface_equivalence"])
    timing, persistence = _validate_receipts(
        payload["timing_receipts"], payload["persistence_receipts"], lane
    )
    artifacts = _mapping(payload["artifacts"], "artifacts")
    if lane == "hermetic" and "hermetic-summary.json" not in artifacts:
        raise BundleError("bundle requires artifacts/hermetic-summary.json")
    validated_artifacts: dict[str, Any] = {}
    for relative, artifact in artifacts.items():
        relative = _canonical_relative(relative)
        validated_artifacts[relative] = _validate_artifact(
            relative, artifact, corpus, lane
        )
    expected_artifacts = _expected_artifact_paths(corpus, lane)
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
    _validate_stability_document(asdict(stability), stability.verdict)
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
            stability,
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
    expected_artifacts = _expected_artifact_paths(corpus, lane)
    if set(artifact_documents) != expected_artifacts:
        raise BundleError("verified artifact coverage is not closed against corpus")
    for relative, document in artifact_documents.items():
        _validate_artifact(relative, document, corpus, lane)
    if lane == "competitive":
        _validate_competitive_documents(
            load_json("competitive/deterministic-metrics.json"),
            load_json("competitive/blinded-comparisons.json"),
            load_json("competitive/verdict.json"),
            corpus["competitive_case_ids"],
            manifest["competitive_verdict"],
            evaluate_stability(
                {
                    profile: {
                        gate: dict(verdict)
                        for gate, verdict in profile_value["gates"].items()
                    }
                    for profile, profile_value in load_json("stability/gates.json")[
                        "profiles"
                    ].items()
                },
                architecture_exceptions=tuple(
                    load_json("stability/gates.json")["architecture_exceptions"]
                ),
            ),
        )

    for relative in sorted(declared_set):
        if relative.endswith(".json"):
            document = load_json(relative)
            _scan_safe(document, relative)
    return manifest
