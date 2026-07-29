"""Pure compiler for sealed live scorecard execution evidence.

This module deliberately has no HTTP, provider, evaluator, database, or environment
dependencies.  It accepts one already-sealed execution document and turns it into
the closed competitive bundle contract.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from .bundle import (
    BundleError,
    _validate_live_diagnostics,
    _validate_local_replay_provenance,
    _validate_provider_result_reconciliation,
    derive_generation,
    write_bundle,
)
from .competitive import VALID_EVALUATOR_VALUES, classify_pair, evaluate_competitive
from .corpus import (
    COMPETITIVE_CASE_MODES,
    HERMETIC_EXTRACTION_CASE_IDS,
    HERMETIC_SEARCH_CASE_IDS,
    validate_corpus,
)
from .stability import StabilityVerdict


class LiveExecutionError(ValueError):
    """Sealed live input is incomplete, inconsistent, or not scoreable."""


_LOCAL_CAPTURE_REPLAY_EXTRACTORS = frozenset(
    {"trafilatura", "crawl4ai", "obscura", "playwright"}
)
_TOP_LEVEL_FIELDS = {
    "schema",
    "run_id",
    "evaluator",
    "topology",
    "sanitized_config_sha256",
    "provider_snapshot",
    "stability_binding",
    "baseline_identity",
    "candidate_identity",
    "captures",
    "operations",
}
_IDENTITY_FIELDS = {
    "commit",
    "image_digest",
    "started_at",
    "finished_at",
}


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode()
    except (TypeError, ValueError) as exc:
        raise LiveExecutionError(
            f"sealed execution is not canonical JSON: {exc}"
        ) from exc


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LiveExecutionError(f"{label} must be an object")
    return value


def _exact(value: Mapping[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise LiveExecutionError(f"{label} must contain exact schema keys")


def _compile_identity(
    raw: object, *, dimensions: Mapping[str, Any], generation: str, label: str
) -> dict[str, Any]:
    identity = _mapping(raw, f"{label} identity")
    _exact(identity, _IDENTITY_FIELDS, f"{label} identity")
    return {
        "generation": generation,
        "commit": identity["commit"],
        "image_digest": identity["image_digest"],
        "sanitized_config_sha256": dimensions["sanitized_config_sha256"],
        "dimensions": dict(dimensions),
        "started_at": identity["started_at"],
        "finished_at": identity["finished_at"],
    }


def _capture_index(
    raw_captures: object, live_extractions: list[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_captures, list) or len(raw_captures) != 4:
        raise LiveExecutionError("sealed execution requires exactly 4 captures")
    expected = {case["id"]: case for case in live_extractions}
    captures: dict[str, dict[str, Any]] = {}
    keys = {"case_id", "snapshot_id", "url", "url_sha256", "capture_sha256"}
    for raw in raw_captures:
        capture = _mapping(raw, "capture")
        _exact(capture, keys, "capture")
        case_id = capture["case_id"]
        if case_id not in expected or case_id in captures:
            raise LiveExecutionError("capture coverage is not exact")
        corpus_case = expected[case_id]
        if (
            capture["snapshot_id"] != corpus_case["snapshot_id"]
            or capture["url"] != corpus_case["url"]
            or capture["url_sha256"] != corpus_case["url_sha256"]
        ):
            raise LiveExecutionError("capture does not match frozen snapshot identity")
        digest = capture["capture_sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise LiveExecutionError("capture content hash must be lowercase SHA-256")
        captures[case_id] = dict(capture)
    if set(captures) != set(expected):
        raise LiveExecutionError("capture coverage is not exact")
    return captures


def _compile_operation(
    raw: object,
    *,
    expected: Mapping[str, Mapping[str, Any]],
    captures: Mapping[str, Mapping[str, Any]],
    evaluator: Mapping[str, Any],
    allowed_providers: set[str],
    topology: Mapping[str, Any],
    identities: Mapping[str, Mapping[str, Any]],
) -> tuple[
    str, dict[str, Any], dict[str, str], list[dict[str, Any]], list[dict[str, Any]]
]:
    operation = _mapping(raw, "live operation")
    _exact(
        operation,
        {"case_id", "mode", "request", "baseline", "candidate", "evaluation"},
        "live operation",
    )
    case_id = operation["case_id"]
    if case_id not in expected or operation["mode"] != COMPETITIVE_CASE_MODES[case_id]:
        raise LiveExecutionError("operation is outside the frozen corpus")
    request = _mapping(operation["request"], f"{case_id} request")
    if operation["mode"] == "extraction":
        _exact(
            request,
            {
                "url",
                "snapshot_id",
                "url_sha256",
                "capture_sha256",
                "replay_chain",
                "caller",
            },
            f"{case_id} request",
        )
        capture = captures[case_id]
        if any(
            request[field] != capture[field]
            for field in ("url", "snapshot_id", "url_sha256", "capture_sha256")
        ):
            raise LiveExecutionError(
                "extraction request does not match frozen snapshot"
            )
        if (
            not isinstance(request["replay_chain"], list)
            or not request["replay_chain"]
            or any(
                not isinstance(extractor, str) or not extractor
                for extractor in request["replay_chain"]
            )
            or len(request["replay_chain"]) != len(set(request["replay_chain"]))
            or not set(request["replay_chain"]) <= _LOCAL_CAPTURE_REPLAY_EXTRACTORS
        ):
            raise LiveExecutionError("local captured replay chain is invalid")
    else:
        _exact(
            request,
            {"query", "free_only", "providers", "caller"},
            f"{case_id} request",
        )
        if request["query"] != expected[case_id]["live_query"]:
            raise LiveExecutionError("search request must use the literal live query")
        if (
            request["free_only"] is not True
            or not isinstance(request["providers"], list)
            or not request["providers"]
            or any(
                not isinstance(provider, str) or not provider
                for provider in request["providers"]
            )
            or not set(request["providers"]) <= allowed_providers
        ):
            raise LiveExecutionError(
                "search request must be closed to ready tier-zero providers"
            )
    if not isinstance(request.get("caller"), str) or not request["caller"]:
        raise LiveExecutionError("live request caller is required")

    baseline = _mapping(operation["baseline"], f"{case_id} baseline")
    candidate = _mapping(operation["candidate"], f"{case_id} candidate")
    evidence_fields = (
        {"outcome", "content", "capture_sha256", "diagnostics"}
        if operation["mode"] == "extraction"
        else {"outcome", "results", "diagnostics"}
    )
    if "diagnostics" not in baseline or "diagnostics" not in candidate:
        raise LiveExecutionError(
            f"{case_id} diagnostics are required for both identities"
        )
    _exact(baseline, evidence_fields, f"{case_id} baseline")
    _exact(candidate, evidence_fields, f"{case_id} candidate")
    for side in (baseline, candidate):
        normalized = (
            [side["content"]] if operation["mode"] == "extraction" else side["results"]
        )
        if operation["mode"] == "extraction" and side["content"] is not None:
            if (
                side["content"].get("url") != captures[case_id]["url"]
                or side["capture_sha256"] != captures[case_id]["capture_sha256"]
            ):
                raise LiveExecutionError(
                    "extraction evidence must match the shared frozen capture"
                )
        if isinstance(normalized, list):
            for item in normalized:
                if isinstance(item, Mapping) and (
                    item.get("egress") != topology["egress"]
                    or item.get("machine") != topology["machine"]
                ):
                    raise LiveExecutionError(
                        "normalized provenance must match the sealed topology"
                    )
    try:
        _validate_live_diagnostics(
            baseline["diagnostics"], f"{case_id} baseline diagnostics"
        )
        _validate_live_diagnostics(
            candidate["diagnostics"], f"{case_id} candidate diagnostics"
        )
    except BundleError as exc:
        raise LiveExecutionError(str(exc)) from exc
    for side_name, side in (("baseline", baseline), ("candidate", candidate)):
        freshness = _mapping(side["diagnostics"], "diagnostics")["freshness"]
        identity = identities[side_name]
        started = datetime.fromisoformat(identity["started_at"].replace("Z", "+00:00"))
        finished = datetime.fromisoformat(
            identity["finished_at"].replace("Z", "+00:00")
        )
        observed = datetime.fromisoformat(
            freshness["observed_at"].replace("Z", "+00:00")
        )
        if not started <= observed <= finished or freshness["age_seconds"] != int(
            (finished - observed).total_seconds()
        ):
            raise LiveExecutionError(
                "freshness observation and age must match the identity execution window"
            )
    for side in (baseline, candidate):
        diagnostics = _mapping(side["diagnostics"], "diagnostics")
        attempts = diagnostics["attempts"]
        if operation["mode"] == "extraction":
            if (
                any(attempt["kind"] != "extractor" for attempt in attempts)
                or {attempt["name"] for attempt in attempts}
                != set(request["replay_chain"])
                or diagnostics["spend"]["provider_calls"] != 0
            ):
                raise LiveExecutionError(
                    "extraction diagnostics must match the local captured replay chain"
                )
            try:
                _validate_local_replay_provenance(
                    side,
                    request["replay_chain"],
                    f"{case_id} extraction evidence",
                )
            except BundleError as exc:
                raise LiveExecutionError(str(exc)) from exc
        else:
            if (
                any(attempt["kind"] != "provider" for attempt in attempts)
                or {attempt["name"] for attempt in attempts}
                != set(request["providers"])
                or any(
                    result["provider"] not in request["providers"]
                    for result in side["results"]
                    if isinstance(result, Mapping)
                )
            ):
                raise LiveExecutionError(
                    "provider evidence must exactly represent the sealed request"
                )
            try:
                _validate_provider_result_reconciliation(
                    side, request["providers"], f"{case_id} provider evidence"
                )
            except BundleError as exc:
                raise LiveExecutionError(str(exc)) from exc

    evaluation = _mapping(operation["evaluation"], f"{case_id} evaluation")
    _exact(evaluation, {"forward", "reverse"}, f"{case_id} evaluation")
    forward, reverse = evaluation["forward"], evaluation["reverse"]
    if evaluator["status"] == "unavailable":
        if (forward, reverse) != ("unavailable", "unavailable"):
            raise LiveExecutionError(
                "unavailable evaluator requires both orders unavailable"
            )
    elif (
        forward not in VALID_EVALUATOR_VALUES
        or reverse not in VALID_EVALUATOR_VALUES
        or "unavailable" in (forward, reverse)
    ):
        raise LiveExecutionError("pinned evaluator outputs are invalid")

    pair = {
        "pair_id": case_id,
        "mode": operation["mode"],
        "forward": forward,
        "reverse": reverse,
    }
    pair["classification"] = classify_pair(pair).classification
    artifact: dict[str, Any] = {
        "schema": (
            "normalized-competitive-extraction-v1"
            if operation["mode"] == "extraction"
            else "normalized-competitive-search-v1"
        ),
        "case_id": case_id,
        "mode": operation["mode"],
        "request": dict(request),
        "baseline": dict(baseline),
        "candidate": dict(candidate),
    }
    if operation["mode"] == "extraction":
        artifact["capture"] = dict(captures[case_id])
    timings = [
        dict(_mapping(side["diagnostics"], "diagnostics")["timing"])
        for side in (baseline, candidate)
    ]
    persistence = []
    for side in (baseline, candidate):
        diagnostic = _mapping(side["diagnostics"], "diagnostics")
        receipt = dict(_mapping(diagnostic["persistence"], "persistence"))
        receipt["operation_id"] = _mapping(diagnostic["timing"], "timing")[
            "operation_id"
        ]
        persistence.append(receipt)
    return case_id, artifact, pair, timings, persistence


def compile_live_execution(
    *,
    sealed: Mapping[str, Any],
    corpus: Mapping[str, Any],
    corpus_sha256: str,
    stability: StabilityVerdict,
    stability_proof: Mapping[str, Any],
    surface_equivalence: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile sealed observations into a competitive bundle payload."""
    sealed = _mapping(sealed, "sealed execution")
    _exact(sealed, _TOP_LEVEL_FIELDS, "sealed execution")
    if sealed["schema"] != "sealed-scorecard-live-execution-v1":
        raise LiveExecutionError("unsupported sealed execution schema")
    validate_corpus(corpus)
    if stability.verdict != "stable":
        raise LiveExecutionError("live compilation requires a stable gate verdict")
    if surface_equivalence.get("status") != "pass":
        raise LiveExecutionError(
            "live compilation requires passing surface equivalence"
        )
    binding = _mapping(sealed["stability_binding"], "stability binding")
    proof = _mapping(stability_proof, "verified stability proof")
    binding_fields = {
        "schema",
        "manifest_sha256",
        "generation",
        "corpus_sha256",
        "sanitized_config_sha256",
        "candidate_commit",
        "candidate_image_digest",
    }
    _exact(binding, binding_fields, "stability binding")
    _exact(proof, binding_fields, "verified stability proof")
    if (
        binding != proof
        or binding["schema"] != "verified-hermetic-stability-binding-v1"
        or binding["corpus_sha256"] != corpus_sha256
    ):
        raise LiveExecutionError(
            "sealed stability binding does not match the verified hermetic proof"
        )
    if corpus_sha256 != sha256(_canonical_bytes(corpus)).hexdigest():
        raise LiveExecutionError(
            "stability-bound corpus hash does not match loaded corpus content"
        )
    candidate = _mapping(sealed["candidate_identity"], "candidate identity")
    if (
        candidate.get("commit") != binding["candidate_commit"]
        or candidate.get("image_digest") != binding["candidate_image_digest"]
        or binding["candidate_image_digest"] is None
    ):
        raise LiveExecutionError(
            "live candidate commit and image must match the stability binding"
        )

    evaluator = _mapping(sealed["evaluator"], "evaluator identity")
    evaluator_keys = {
        "status",
        "model",
        "prompt_sha256",
        "settings_sha256",
        "reason_code",
    }
    _exact(evaluator, evaluator_keys, "evaluator identity")
    provider_snapshot = _mapping(sealed["provider_snapshot"], "provider snapshot")
    providers = provider_snapshot.get("providers")
    if (
        provider_snapshot.get("schema") != "normalized-provider-snapshot-v1"
        or provider_snapshot.get("profile") != "free"
        or not isinstance(providers, list)
        or not providers
    ):
        raise LiveExecutionError("free provider snapshot is required")
    allowed_providers = {
        provider["provider"]
        for provider in providers
        if isinstance(provider, Mapping)
        and provider.get("tier") == 0
        and provider.get("status") == "ready"
        and isinstance(provider.get("provider"), str)
    }
    if len(allowed_providers) != len(providers):
        raise LiveExecutionError(
            "provider snapshot must contain only ready tier-zero providers"
        )

    captures = _capture_index(sealed["captures"], corpus["live_extractions"])
    dimensions = {
        "corpus_hashes": {
            "corpus.json": corpus_sha256,
            "stability/manifest.json": binding["manifest_sha256"],
            "stability/generation.txt": sha256(
                binding["generation"].encode()
            ).hexdigest(),
            "stability/sanitized-config.txt": sha256(
                binding["sanitized_config_sha256"].encode()
            ).hexdigest(),
            **{
                f"captures/{case_id}.bin": capture["capture_sha256"]
                for case_id, capture in sorted(captures.items())
            },
        },
        "evaluator": dict(evaluator),
        "topology": dict(_mapping(sealed["topology"], "topology")),
        "profile": "free",
        "provider_snapshot_sha256": sha256(
            _canonical_bytes(provider_snapshot)
        ).hexdigest(),
        "sanitized_config_sha256": sealed["sanitized_config_sha256"],
    }
    topology = dimensions["topology"]
    try:
        generation = derive_generation(dimensions)
    except BundleError as exc:
        raise LiveExecutionError(str(exc)) from exc
    expected = {
        **{case["id"]: case for case in corpus["search_intents"]},
        **{case["id"]: case for case in corpus["live_extractions"]},
    }
    operations = sealed["operations"]
    if not isinstance(operations, list) or len(operations) != 28:
        raise LiveExecutionError("sealed execution must contain exactly 28 operations")

    artifacts: dict[str, Any] = {}
    pairs: list[dict[str, str]] = []
    timings: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    seen: set[str] = set()
    identities = {
        "baseline": _mapping(sealed["baseline_identity"], "baseline identity"),
        "candidate": _mapping(sealed["candidate_identity"], "candidate identity"),
    }
    for raw in operations:
        case_id, artifact, pair, operation_timings, operation_receipts = (
            _compile_operation(
                raw,
                expected=expected,
                captures=captures,
                evaluator=evaluator,
                allowed_providers=allowed_providers,
                topology=topology,
                identities=identities,
            )
        )
        if case_id in seen:
            raise LiveExecutionError("live operation ids must be unique")
        seen.add(case_id)
        prefix = (
            "extractions"
            if COMPETITIVE_CASE_MODES[case_id] == "extraction"
            else "searches"
        )
        artifacts[f"{prefix}/{case_id}.json"] = artifact
        pairs.append(pair)
        timings.extend(operation_timings)
        receipts.extend(operation_receipts)
    if seen != set(COMPETITIVE_CASE_MODES):
        raise LiveExecutionError("live execution coverage is not closed")
    if len({timing["operation_id"] for timing in timings}) != 56:
        raise LiveExecutionError("timing operation ids must be unique")
    if len({receipt["durable_id"] for receipt in receipts}) != 56:
        raise LiveExecutionError("persistence durable ids must be unique")

    ordered_pairs = [
        next(pair for pair in pairs if pair["pair_id"] == case_id)
        for case_id in COMPETITIVE_CASE_MODES
    ]
    competitive = evaluate_competitive(
        stability,
        [
            {
                "pair_id": pair["pair_id"],
                "mode": pair["mode"],
                "forward": pair["forward"],
                "reverse": pair["reverse"],
            }
            for pair in ordered_pairs
        ],
    )
    corpus_manifest = {
        "version": corpus["version"],
        "hashes": dimensions["corpus_hashes"],
        "hermetic_search_case_ids": list(HERMETIC_SEARCH_CASE_IDS),
        "hermetic_extraction_case_ids": list(HERMETIC_EXTRACTION_CASE_IDS),
        "competitive_case_ids": list(COMPETITIVE_CASE_MODES),
        "dimensions": dimensions,
        "generation": generation,
    }
    return {
        "run_id": sealed["run_id"],
        "generation": generation,
        "candidate_identity": _compile_identity(
            sealed["candidate_identity"],
            dimensions=dimensions,
            generation=generation,
            label="candidate",
        ),
        "baseline_identity": _compile_identity(
            sealed["baseline_identity"],
            dimensions=dimensions,
            generation=generation,
            label="baseline",
        ),
        "corpus": corpus_manifest,
        "provider_snapshot": dict(provider_snapshot),
        "surface_equivalence": dict(surface_equivalence),
        "timing_receipts": {
            "schema": "normalized-timing-receipts-v1",
            "operations": timings,
        },
        "persistence_receipts": {
            "schema": "normalized-persistence-receipts-v1",
            "status": "accepted",
            "reason": "sealed authority receipts verified for both identities",
            "receipts": receipts,
        },
        "artifacts": artifacts,
        "competitive": {
            "deterministic_metrics": {
                "schema": "competitive-deterministic-metrics-v1",
                "consistent_pairs": competitive.consistent_pairs,
                "decisive_pairs": competitive.candidate_wins
                + competitive.baseline_wins,
                "candidate_wins": competitive.candidate_wins,
                "baseline_wins": competitive.baseline_wins,
                "p_value": competitive.p_value,
            },
            "blinded_comparisons": {
                "schema": "blinded-comparisons-v1",
                "pairs": ordered_pairs,
            },
            "verdict": {
                "schema": "competitive-verdict-v1",
                "verdict": competitive.verdict,
                "reason": competitive.reason,
            },
        },
    }


def write_live_execution_bundle(
    output: Path,
    *,
    sealed: Mapping[str, Any],
    corpus: Mapping[str, Any],
    corpus_sha256: str,
    stability: StabilityVerdict,
    stability_proof: Mapping[str, Any],
    surface_equivalence: Mapping[str, Any],
) -> Path:
    """Compile and atomically write a sealed live execution bundle."""
    try:
        payload = compile_live_execution(
            sealed=sealed,
            corpus=corpus,
            corpus_sha256=corpus_sha256,
            stability=stability,
            stability_proof=stability_proof,
            surface_equivalence=surface_equivalence,
        )
        return write_bundle(
            output, lane="competitive", stability=stability, payload=payload
        )
    except BundleError as exc:
        raise LiveExecutionError(str(exc)) from exc
