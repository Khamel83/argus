"""Separate, checksummed ``argus-acceptance-v3/free-targeted`` bundles.

This module is intentionally independent from :mod:`argus.scorecard.bundle`.
The v2 stability bundle has a different 22-gate contract and must retain its
existing semantics and bytes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any
from urllib.parse import urlsplit

from .contract import canonical_bytes, canonical_hash
from .readiness import evaluate_readiness_gates, project_readiness_evidence


class BundleError(ValueError):
    """A v3 evidence bundle is incomplete, inconsistent, or unsafe."""


SCHEMA = "argus-acceptance-v3/free-targeted"
EIGHT_GATES = (
    "build_identity",
    "canonical_access",
    "authority_policy",
    "transport_equivalence",
    "delivery",
    "research_completion",
    "evidence_minimum",
    "regression",
)
FROZEN_GATE_DEFINITIONS = {
    "build_identity": "API and MCP run one immutable digest, full source revision, and package version with documented rollback.",
    "canonical_access": "Live/startup/ready and canonical HTTPS clients pass; unauthenticated MCP rejects without side effects.",
    "authority_policy": "PostgreSQL/evidence authority and free-only caller policy hold with no forbidden spend or unresolved charge.",
    "transport_equivalence": "Authenticated HTTP and MCP preserve the same run/status/artifact semantics for the canary.",
    "delivery": "Maya canary is durably stored and exact replay is a duplicate while bounded delivery drains.",
    "research_completion": "The benchmark completes within the bound and safe status/report/manifest projections are readable and secret-free.",
    "evidence_minimum": "At least five usable URLs, three registrable domains, two primary sources, and closed citation evidence are present.",
    "regression": "Focused/full/architecture checks and production log/canary windows have no unexpected failure or auth loops.",
}
RUBRIC_CELLS = (
    ("source_citation_integrity", 25),
    ("coverage_diversity", 15),
    ("factual_discipline", 15),
    ("decision_usefulness", 15),
    ("execution_delivery", 20),
    ("provenance_cost_truth", 10),
)
_COMPLETED_REQUIRED_REQUIREMENTS = 15
_COMPLETED_MIN_URLS = 5
_COMPLETED_MIN_DOMAINS = 3
_COMPLETED_MIN_PRIMARY = 2
TERMINAL_BRANCHES = frozenset(
    {
        "completed",
        "pre_artifact_not_run",
        "evaluator_not_run",
        "preflight_failed",
        "FAIL",
        "rollback_incomplete",
    }
)
FROZEN_GATE_DEFINITIONS_SHA256 = (
    "067161ca8c97ce290c2ac7f7bbd93c868e4c9d07ddd81588f876718ea0d3cd80"
)
FROZEN_RUBRIC_SHA256 = (
    "9922c3d77999860e8df3435b8cd827cdb5a47fd84cbda92666d028689484f76e"
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SENSITIVE_TEXT = re.compile(
    r"(?:\bbearer\s+|\bbasic\s+|\bcookie\s*[:=]|\b(?:password|secret|api[_-]?key|authorization|private[_-]?key|raw[_-]?exception|stacktrace)\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:traceback|exception|stack trace)\b|(?:postgres(?:ql)?|pg)://|\bssh://|\bfile://)",
    re.IGNORECASE,
)
_OPAQUE_KEY_NAMES = {
    "runid",
    "captureid",
    "idempotencykey",
    "requestid",
    "sessionid",
    "operationid",
    "receiptid",
}
_ABSOLUTE_PATH = re.compile(
    r"(?:^|[\s'(\[])(?:/(?:Users|Volumes|private|tmp|var|opt|srv|etc|root|usr|Applications|Library|System|bin|sbin|proc|data|workspace|mnt|run|media)/|[A-Za-z]:[\\/]|\\\\|~/(?:\.\.?/)?|\.\.?/|%2f(?:Users|Volumes|private|tmp|var|opt|srv|etc|root|usr)(?:%2f|/))",
    re.IGNORECASE,
)


def _safe_text(value: str, *, location: str) -> None:
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise BundleError(f"control character at {location}")
    if _SENSITIVE_TEXT.search(value) or _ABSOLUTE_PATH.search(value):
        raise BundleError(f"sensitive/local value at {location}")


def _safe_bytes(value: bytes, *, location: str) -> str:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError(f"{location} is not safe UTF-8 text") from exc
    _safe_text(text, location=location)
    return text


def _safe_json(value: Any, *, location: str = "document") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise BundleError(f"non-string key at {location}")
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in {
                "authorization",
                "authorizationheader",
                "bearer",
                "credential",
                "password",
                "secret",
                "secretvalue",
                "token",
                "accesstoken",
                "sessiontoken",
                "sshkey",
                "privatekey",
                "rawexception",
                "stacktrace",
                "traceback",
                "exception",
                "apikey",
                "cookie",
                "rawpayload",
                "nativepayload",
            }:
                raise BundleError(f"sensitive field at {location}.{key}")
            if normalized in _OPAQUE_KEY_NAMES:
                raise BundleError(
                    f"opaque identifier must be hashed at {location}.{key}"
                )
            _safe_json(nested, location=f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _safe_json(nested, location=f"{location}[{index}]")
    elif isinstance(value, str):
        _safe_text(value, location=location)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"invalid JSON file {path.name}") from exc


def _relative(path: str) -> str:
    if not isinstance(path, str) or not path or "\\" in path:
        raise BundleError("bundle paths must be canonical relative POSIX paths")
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.as_posix() != path
    ):
        raise BundleError("bundle path escapes root")
    return path


def _safe_path(root: Path, relative: str) -> Path:
    path = root / _relative(relative)
    cursor = root
    for part in PurePosixPath(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BundleError("bundle path contains a symlink")
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise BundleError("bundle path escapes root") from exc
    return path


def evaluate_gates(
    gates: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate the separate v3 eight-gate tuple and derive its verdict."""

    if isinstance(gates, Mapping):
        if set(gates) != set(EIGHT_GATES):
            raise BundleError("gates must contain exactly the eight v3 gates")
        normalized = {name: dict(gates[name]) for name in EIGHT_GATES}
    else:
        normalized = {}
        for item in gates:
            if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
                raise BundleError("gate list item is invalid")
            name = item["name"]
            if name in normalized:
                raise BundleError("duplicate gate")
            normalized[name] = {
                key: value for key, value in item.items() if key != "name"
            }
        if set(normalized) != set(EIGHT_GATES):
            raise BundleError("gates must contain exactly the eight v3 gates")
    for name in EIGHT_GATES:
        gate = normalized[name]
        if set(gate) != {"status", "reason", "evidence"}:
            raise BundleError(f"gate {name} has unexpected fields")
        if gate.get("status") not in {"PASS", "FAIL", "PENDING"}:
            raise BundleError(f"invalid status for gate {name}")
        if (
            not isinstance(gate.get("reason"), str)
            or not gate["reason"]
            or len(gate["reason"]) > 500
        ):
            raise BundleError(f"gate {name} requires a bounded reason")
        evidence = gate.get("evidence")
        if (
            not isinstance(evidence, list)
            or not evidence
            or any(not isinstance(item, str) for item in evidence)
        ):
            raise BundleError(f"gate {name} requires evidence locators")
    statuses = [normalized[name]["status"] for name in EIGHT_GATES]
    if canonical_hash(FROZEN_GATE_DEFINITIONS) != FROZEN_GATE_DEFINITIONS_SHA256:
        raise BundleError("frozen v3 gate definitions changed")
    verdict = "PASS" if all(status == "PASS" for status in statuses) else "FAIL"
    return {
        "schema": "argus-acceptance-v3/gates",
        "definitions_sha256": FROZEN_GATE_DEFINITIONS_SHA256,
        "gates": normalized,
        "verdict": verdict,
        "passed": statuses.count("PASS"),
    }


def evaluate_readiness(
    accepted: object,
    *,
    level: str | object | None = None,
    delivery_requested: bool = False,
    claims_maya_integration: bool = False,
    full_fleet: bool = False,
    outbox: object | None = None,
    maya_receipt: object | None = None,
    hard_gates: Mapping[str, object] | None = None,
    score: int | float | None = None,
    health: object | None = None,
) -> dict[str, Any]:
    """Evaluate conditional readiness and return its safe evidence projection.

    The eight-gate acceptance bundle remains unchanged.  This separate
    evaluator records whether the durable core result is ready, whether a
    requested outbox delivery is pending, or whether a correlated Maya receipt
    completes the full-fleet claim.
    """

    verdict = evaluate_readiness_gates(
        accepted,
        level=level,
        delivery_requested=delivery_requested,
        claims_maya_integration=claims_maya_integration,
        full_fleet=full_fleet,
        outbox=outbox,
        maya_receipt=maya_receipt,
        hard_gates=hard_gates,
        score=score,
        health=health,
    )
    # Exercise the bounded projection here so callers that write a v3 bundle
    # cannot accidentally persist raw identity values from the full verdict.
    project_readiness_evidence(verdict)
    return verdict


def calculate_score(score: Mapping[str, Any]) -> int:
    """Recompute the six-cell frozen score; ``not_run`` has no numeric zero."""

    if canonical_hash(dict(RUBRIC_CELLS)) != FROZEN_RUBRIC_SHA256:
        raise BundleError("frozen v3 rubric changed")
    if score.get("status") == "not_run":
        raise BundleError("score is not_run")
    if score.get("status") != "scored":
        raise BundleError("score status must be scored or not_run")
    cells = score.get("cells")
    if not isinstance(cells, Mapping) or set(cells) != {
        name for name, _ in RUBRIC_CELLS
    }:
        raise BundleError("score cells do not match the six frozen rubric cells")
    total = 0
    for name, maximum in RUBRIC_CELLS:
        value = cells[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= maximum
        ):
            raise BundleError(f"score cell {name} is outside its frozen bound")
        total += value
    if score.get("total", total) != total:
        raise BundleError("score arithmetic mismatch")
    return total


def terminal_sections(status: str, reason: str) -> dict[str, Any]:
    """Build the non-fabricating status documents for a terminal branch."""

    if status not in TERMINAL_BRANCHES or not isinstance(reason, str) or not reason:
        raise BundleError("unknown terminal branch")
    if status == "completed":
        return {
            "artifact": "required",
            "claim_support": "required",
            "synthesis": "required",
            "scoring": "required",
            "score": None,
            "claim_support_document": None,
        }
    if status == "evaluator_not_run":
        return {
            "artifact": "required",
            "claim_support": "not_run",
            "synthesis": "not_run",
            "scoring": "not_run",
            "score": {"status": "not_run", "reason": reason, "cells": None},
            "claim_support_document": {
                "status": "not_run",
                "reason": reason,
                "requirements": None,
            },
        }
    return {
        "artifact": "not_run",
        "claim_support": "not_run",
        "synthesis": "not_run",
        "scoring": "not_run",
        "score": {"status": "not_run", "reason": reason, "cells": None},
        "claim_support_document": {
            "status": "not_run",
            "reason": reason,
            "requirements": None,
        },
    }


_SECTION_NAMES = {"artifact", "claim_support", "synthesis", "scoring"}


def _validate_terminal_documents(
    *,
    status: str,
    sections: Mapping[str, Any],
    score: Mapping[str, Any],
    claim_support: Mapping[str, Any],
) -> None:
    """Enforce the status/section/document state machine before publication."""

    if set(sections) != _SECTION_NAMES:
        raise BundleError("manifest sections are incomplete")
    if status == "completed":
        if any(
            not isinstance(value, str) or not value or value == "not_run"
            for value in sections.values()
        ):
            raise BundleError("completed bundle requires every artifact section")
        if score.get("status") != "scored":
            raise BundleError("completed bundle requires scored evaluation")
        if claim_support.get("status") != "scored":
            raise BundleError("completed bundle requires scored claim support")
        return
    if status == "evaluator_not_run":
        if (
            not isinstance(sections["artifact"], str)
            or not sections["artifact"]
            or sections["artifact"] == "not_run"
            or any(sections[key] != "not_run" for key in _SECTION_NAMES - {"artifact"})
        ):
            raise BundleError(
                "evaluator_not_run must retain a completed artifact and not_run evaluation"
            )
    elif any(value != "not_run" for value in sections.values()):
        raise BundleError("pre-artifact terminal branch cannot fabricate sections")
    if score.get("status") != "not_run" or score.get("cells") is not None:
        raise BundleError("terminal non-completed branch requires not_run score")
    if (
        claim_support.get("status") != "not_run"
        or claim_support.get("requirements") is not None
    ):
        raise BundleError("terminal non-completed branch requires not_run claims")


def _locator_candidates(locator: str) -> tuple[str, ...]:
    if locator.startswith("artifacts/"):
        return (locator,)
    return (locator, f"artifacts/{locator}")


def _locator_exists(locator: object, files: Mapping[str, bytes]) -> bool:
    if not isinstance(locator, str) or not locator or locator == "not_run":
        return False
    try:
        candidates = _locator_candidates(_relative(locator))
    except BundleError:
        return False
    return any(candidate in files for candidate in candidates)


def _validate_section_locators(
    sections: Mapping[str, Any], files: Mapping[str, bytes], *, status: str
) -> None:
    if status not in {"completed", "evaluator_not_run"}:
        return
    for name, locator in sections.items():
        if locator == "not_run":
            continue
        if not _locator_exists(locator, files):
            raise BundleError(f"section locator does not exist: {name}")


def _validate_recovery(value: object) -> None:
    if not isinstance(value, Mapping):
        raise BundleError("recovery evidence must be an object")
    status = value.get("status")
    if status not in {"not_applicable", "complete", "failed", "not_run"}:
        raise BundleError("recovery evidence status is invalid")
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason or len(reason) > 500:
        raise BundleError("recovery evidence reason is invalid")
    proof = value.get("proof")
    proof_sha256 = value.get("proof_sha256")
    if not isinstance(proof, str) or not proof:
        raise BundleError("recovery evidence proof is required")
    if not isinstance(proof_sha256, str) or not _SHA256.fullmatch(proof_sha256):
        raise BundleError("recovery evidence proof hash is required")
    if proof_sha256 != canonical_hash(proof):
        raise BundleError("recovery evidence proof hash mismatch")
    if status == "not_applicable":
        if value.get("no_change") is not True or value.get("change_count") != 0:
            raise BundleError("not_applicable recovery needs explicit no-change proof")
        before = value.get("before_sha256")
        after = value.get("after_sha256")
        if (
            not isinstance(before, str)
            or not _SHA256.fullmatch(before)
            or before != after
        ):
            raise BundleError("not_applicable recovery hashes must be identical")
    if status == "complete":
        for key in (
            "backup_sha256",
            "restore_sha256",
            "schema_sha256",
            "identity_sha256",
            "soak_sha256",
        ):
            if not isinstance(value.get(key), str) or not _SHA256.fullmatch(value[key]):
                raise BundleError(f"complete recovery is missing {key}")


def derive_verdict(
    *,
    status: str,
    gates_verdict: str,
    score: Mapping[str, Any],
    claim_support: Mapping[str, Any],
    artifact_complete: bool,
) -> str:
    """Derive a literal verdict from immutable documents, never a caller label."""

    if status == "rollback_incomplete":
        return "rollback_incomplete"
    if status == "pre_artifact_not_run":
        return "not_run"
    if status in {"evaluator_not_run", "preflight_failed", "FAIL"}:
        return "FAIL"
    if status != "completed" or not artifact_complete:
        return "FAIL"
    if gates_verdict != "PASS":
        return "FAIL"
    if score.get("status") != "scored" or claim_support.get("status") != "scored":
        return "FAIL"
    if calculate_score(score) < 85:
        return "FAIL"
    return "PASS"


def _registrable_domain(host: str) -> str:
    host = host.lower().rstrip(".")
    if not host or "." not in host:
        return host
    labels = host.split(".")
    # The project supports the tld package, but this bounded fallback also
    # handles hermetic fixture domains without doing any network lookup.
    try:
        from tld import get_fld

        value = get_fld(host, fail_silently=True)
        if value:
            return value.lower()
    except Exception:  # pragma: no cover - optional parser fallback
        pass
    return ".".join(labels[-2:])


def minimum_evidence(
    sources: Sequence[Mapping[str, Any]],
    *,
    required_requirements: int,
    covered_requirements: int,
    min_urls: int = 5,
    min_domains: int = 3,
    min_primary: int = 2,
) -> dict[str, Any]:
    """Recompute evidence floors from accepted manifest source rows."""

    accepted: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    source_hashes: set[str] = set()
    for source in sources:
        if source.get("disposition") != "usable":
            continue
        url = source.get("url")
        if not isinstance(url, str) or url in seen:
            continue
        parsed = urlsplit(url)
        if not _is_public_https_url(parsed):
            continue
        required = {
            "provider",
            "extractor",
            "egress",
            "machine",
            "source_type",
            "degraded",
            "source_text_sha256",
            "citation_id",
        }
        if not required.issubset(source) or any(
            not source.get(key) for key in required - {"degraded"}
        ):
            raise BundleError("usable source lacks complete provenance")
        if not isinstance(source["degraded"], bool):
            raise BundleError("usable source degraded label is invalid")
        if not isinstance(source["source_text_sha256"], str) or not _SHA256.fullmatch(
            source["source_text_sha256"]
        ):
            raise BundleError("usable source text hash is invalid")
        if not isinstance(source["citation_id"], str) or not source["citation_id"]:
            raise BundleError("usable source citation ID is invalid")
        accepted.append(source)
        seen.add(url)
        source_hashes.add(source["source_text_sha256"])
    domains = {
        _registrable_domain(urlsplit(source["url"]).hostname or "")
        for source in accepted
    }
    primary = sum(1 for source in accepted if source.get("primary") is True)
    result = {
        "usable_sources": len(accepted),
        "registrable_domains": len(domains),
        "primary_sources": primary,
        "required_requirements": required_requirements,
        "covered_requirements": covered_requirements,
        "closure_leaks": 0,
        "degraded_unlabelled": 0,
        "source_text_sha256": sorted(source_hashes),
    }
    if (
        len(accepted) < min_urls
        or len(domains) < min_domains
        or primary < min_primary
        or covered_requirements < required_requirements
    ):
        raise BundleError("minimum evidence floor failed")
    return result


def _manifest_claim_bindings(
    manifest: Mapping[str, Any], sources: Sequence[Mapping[str, Any]]
) -> tuple[set[str], dict[str, str]]:
    requirements = manifest.get("target_requirements")
    if (
        not isinstance(requirements, list)
        or len(requirements) != _COMPLETED_REQUIRED_REQUIREMENTS
        or any(not isinstance(item, str) or not item for item in requirements)
        or len(set(requirements)) != len(requirements)
    ):
        raise BundleError("manifest must declare exactly 15 target requirements")
    citation_urls = manifest.get("citation_urls")
    if not isinstance(citation_urls, Mapping):
        raise BundleError("manifest must declare citation URL bindings")
    if set(citation_urls) != {f"S{i}" for i in range(_COMPLETED_REQUIRED_REQUIREMENTS)}:
        raise BundleError("manifest citation IDs are incomplete")
    source_urls = {
        source.get("url") for source in sources if source.get("disposition") == "usable"
    }
    normalized: dict[str, str] = {}
    for citation_id, url in citation_urls.items():
        if not isinstance(url, str) or not _is_public_https_url(urlsplit(url)):
            raise BundleError("manifest citation URL is not public HTTPS")
        if url not in source_urls:
            raise BundleError("manifest citation URL is not bound to a source")
        normalized[citation_id] = url
    return set(requirements), normalized


def _is_public_https_url(parsed: Any) -> bool:
    """Return whether a source URL is safe to count toward evidence floors."""

    if (
        parsed.scheme.lower() != "https"
        or parsed.username
        or parsed.password
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
    ):
        return False
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".local", ".internal", ".localhost", ".test", ".invalid", ".example")
    ):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." in host and all(
            label and len(label) <= 63 for label in host.split(".")
        )
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    )


def validate_claim_support(
    value: Mapping[str, Any],
    *,
    required_requirements: int = 15,
    source_hashes: set[str] | None = None,
    requirement_ids: set[str] | None = None,
    citation_urls: Mapping[str, str] | None = None,
) -> None:
    if value.get("status") not in {"scored", "not_run"}:
        raise BundleError("claim support status is invalid")
    if value["status"] == "not_run":
        if value.get("requirements") is not None:
            raise BundleError(
                "not_run claim support cannot contain fabricated requirements"
            )
        return
    rows = value.get("requirements")
    if not isinstance(rows, list) or len(rows) != required_requirements:
        raise BundleError("claim support must bind every target requirement")
    seen: set[str] = set()
    citation_seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise BundleError("claim support row is invalid")
        identifier = row.get("requirement_id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise BundleError("claim support requirement IDs must be unique")
        seen.add(identifier)
        if requirement_ids is not None and identifier not in requirement_ids:
            raise BundleError("claim support requirement ID is not in the manifest")
        if row.get("disposition") not in {"supported", "partial", "unsupported"}:
            raise BundleError("claim support disposition is invalid")
        if (
            not isinstance(row.get("reason"), str)
            or len(row["reason"]) > 300
            or any(ord(char) < 32 for char in row["reason"])
        ):
            raise BundleError("claim support reason is outside bound")
        if not isinstance(row.get("source_text_sha256"), str) or not _SHA256.fullmatch(
            row["source_text_sha256"]
        ):
            raise BundleError("claim support source hash is invalid")
        if not isinstance(row.get("citation_id"), str) or not row["citation_id"]:
            raise BundleError("claim support citation ID is required")
        if row["citation_id"] in citation_seen:
            raise BundleError("claim support citation IDs must be unique")
        citation_seen.add(row["citation_id"])
        if citation_urls is not None and row["citation_id"] not in citation_urls:
            raise BundleError("claim support citation ID is not in the manifest")
        citation_url = row.get("citation_url")
        if not isinstance(citation_url, str) or not _is_public_https_url(
            urlsplit(citation_url)
        ):
            raise BundleError("claim support citation URL is required")
        if (
            citation_urls is not None
            and citation_urls[row["citation_id"]] != citation_url
        ):
            raise BundleError("claim support citation URL does not match the manifest")
        if source_hashes is not None and row["source_text_sha256"] not in source_hashes:
            raise BundleError("claim support source hash is not bound to a source")
        evaluator = row.get("evaluator")
        if (
            not isinstance(evaluator, Mapping)
            or evaluator.get("model") != "gpt-5.6-sol"
            or not _SHA256.fullmatch(str(evaluator.get("prompt_sha256", "")))
            or not _SHA256.fullmatch(str(evaluator.get("settings_sha256", "")))
            or not _SHA256.fullmatch(str(evaluator.get("run_receipt_sha256", "")))
        ):
            raise BundleError("claim support evaluator identity is invalid")
    if requirement_ids is not None and seen != requirement_ids:
        raise BundleError("claim support requirement set does not match manifest")
    if citation_urls is not None and citation_seen != set(citation_urls):
        raise BundleError("claim support citation set does not match manifest")


def build_canary_fixture(
    nonce: str,
    *,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Return exact fresh canary request/body bytes and their hashes."""

    if not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", nonce):
        raise BundleError("canary nonce is invalid")
    query = f"argus-acceptance-v3-canary-{nonce}"
    search_body = {
        "query": query,
        "mode": "discovery",
        "max_results": 1,
        "providers": ["github"],
        "free_only": True,
        "caller": "tonight-acceptance-v3-canary",
    }
    if started_at is None and completed_at is None:
        started_at = completed_at = (
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
    if started_at is None or completed_at is None or started_at != completed_at:
        raise BundleError(
            "canary started_at and completed_at must be identical aware UTC values"
        )
    try:
        parsed_time = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleError("canary timestamps must be aware UTC values") from exc
    if (
        parsed_time.tzinfo is None
        or parsed_time.utcoffset() is None
        or parsed_time.utcoffset().total_seconds() != 0
    ):
        raise BundleError("canary timestamps must be aware UTC values")
    maya_body = {
        "idempotency_key": f"argus-acceptance-v3-{nonce}",
        "query": query,
        "mode": "discovery",
        "started_at": started_at,
        "completed_at": completed_at,
        "summary": "Argus acceptance v3 canary",
        "provenance": {
            "providers": ["github"],
            "egress": "unknown",
            "machine": "argus-acceptance-v3",
            "source_type": "search",
        },
        "pages": [],
    }
    return {
        "query": query,
        "query_sha256": canonical_hash(query),
        "search_body": search_body,
        "search_body_sha256": canonical_hash(search_body),
        "maya_body": maya_body,
        "maya_body_sha256": canonical_hash(maya_body),
        "idempotency_key_sha256": canonical_hash(maya_body["idempotency_key"]),
        "attempts": {"search": 1, "maya": 2},
    }


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BundleError("short bundle write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_bundle(output: Path | str, payload: Mapping[str, Any]) -> None:
    """Atomically publish a v3 private evidence bundle exactly once."""

    target = Path(output)
    if target.exists() or target.is_symlink():
        raise BundleError("bundle output already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    required = {"manifest", "gates", "score", "claim_support", "recovery", "artifacts"}
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise BundleError("bundle payload has unexpected sections")
    manifest = dict(payload["manifest"])
    if manifest.get("schema") != SCHEMA:
        raise BundleError("manifest schema is not acceptance v3")
    status = manifest.get("status")
    if status not in TERMINAL_BRANCHES:
        raise BundleError("manifest status is not a v3 terminal branch")
    sections = manifest.get("sections")
    if not isinstance(sections, Mapping) or set(sections) != {
        "artifact",
        "claim_support",
        "synthesis",
        "scoring",
    }:
        raise BundleError(
            "manifest must declare all artifact/synthesis/scoring sections"
        )
    if (
        manifest.get("competitive_baseline") != "not_applicable"
        or manifest.get("competitive_pair") != "not_applicable"
    ):
        raise BundleError("competitive sections must be explicit not_applicable")
    gates = evaluate_gates(payload["gates"])
    score_value = payload["score"]
    score_not_run = (
        isinstance(score_value, Mapping) and score_value.get("status") == "not_run"
    )
    if not score_not_run:
        calculate_score(score_value)
    else:
        if score_value.get("cells") is not None:
            raise BundleError("not_run score must not have rubric cells")
    claim_support_value = payload["claim_support"]
    if not isinstance(claim_support_value, Mapping):
        raise BundleError("claim support must be an object")
    _validate_terminal_documents(
        status=status,
        sections=sections,
        score=score_value,
        claim_support=claim_support_value,
    )
    _validate_recovery(payload["recovery"])
    evidence_result: dict[str, Any] | None = None
    if status == "completed":
        sources = manifest.get("sources")
        if not isinstance(sources, list):
            raise BundleError("completed bundle requires manifest source rows")
        required_requirements = manifest.get(
            "required_requirements", _COMPLETED_REQUIRED_REQUIREMENTS
        )
        covered_requirements = manifest.get(
            "covered_requirements", _COMPLETED_REQUIRED_REQUIREMENTS
        )
        if (
            isinstance(required_requirements, bool)
            or not isinstance(required_requirements, int)
            or isinstance(covered_requirements, bool)
            or not isinstance(covered_requirements, int)
        ):
            raise BundleError("manifest requirement counts are invalid")
        if (
            required_requirements != _COMPLETED_REQUIRED_REQUIREMENTS
            or covered_requirements != _COMPLETED_REQUIRED_REQUIREMENTS
        ):
            raise BundleError("completed bundle must cover all 15 requirements")
        evidence_result = minimum_evidence(
            sources,
            required_requirements=required_requirements,
            covered_requirements=covered_requirements,
            min_urls=_COMPLETED_MIN_URLS,
            min_domains=_COMPLETED_MIN_DOMAINS,
            min_primary=_COMPLETED_MIN_PRIMARY,
        )
        requirement_ids, citation_urls = _manifest_claim_bindings(manifest, sources)
        validate_claim_support(
            claim_support_value,
            required_requirements=required_requirements,
            source_hashes=set(evidence_result["source_text_sha256"]),
            requirement_ids=requirement_ids,
            citation_urls=citation_urls,
        )
        manifest["evidence_minimum"] = evidence_result
    else:
        validate_claim_support(claim_support_value)
    _safe_json(manifest)
    _safe_json(gates)
    _safe_json(score_value)
    _safe_json(claim_support_value)
    _safe_json(payload["recovery"])
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=str(target.parent))
    )
    try:
        os.chmod(staging, 0o700)
        files: dict[str, bytes] = {
            "manifest.json": canonical_bytes(manifest),
            "gates.json": canonical_bytes(gates),
            "score.json": canonical_bytes(score_value),
            "claim-support.json": canonical_bytes(payload["claim_support"]),
            "recovery-evidence.json": canonical_bytes(payload["recovery"]),
        }
        artifacts = payload["artifacts"]
        if not isinstance(artifacts, Mapping):
            raise BundleError("artifacts must be a path-to-bytes mapping")
        for relative, value in artifacts.items():
            relative = _relative(relative)
            if not relative.startswith("artifacts/"):
                relative = f"artifacts/{relative}"
            if relative in files or relative == "checksums.sha256":
                raise BundleError("artifact path collides with reserved bundle file")
            if not isinstance(value, bytes):
                raise BundleError("artifact payload must be bytes")
            if len(value) > 4 * 1024 * 1024:
                raise BundleError("artifact exceeds 4 MiB bound")
            text = _safe_bytes(value, location=relative)
            if relative.endswith(".json"):
                try:
                    parsed = json.loads(text)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BundleError(
                        f"artifact {relative} is not strict JSON"
                    ) from exc
                _safe_json(parsed, location=relative)
            files[relative] = value
        _validate_section_locators(sections, files, status=status)
        if status in {"completed", "evaluator_not_run"}:
            for name, gate in gates["gates"].items():
                if any(
                    not _locator_exists(locator, files) for locator in gate["evidence"]
                ):
                    raise BundleError(f"gate {name} evidence locator does not exist")
        manifest["files"] = [*sorted(files), "checksums.sha256"]
        _safe_json(manifest)
        files["manifest.json"] = canonical_bytes(manifest)
        for relative, data in files.items():
            _write_bytes(_safe_path(staging, relative), data)
        _fsync_dir(staging)
        lines = []
        for relative in sorted(files):
            digest = hashlib.sha256(files[relative]).hexdigest()
            lines.append(f"{digest}  {relative}\n")
        checksum = "".join(lines).encode("utf-8")
        _write_bytes(_safe_path(staging, "checksums.sha256"), checksum)
        _fsync_dir(staging)
        os.replace(staging, target)
        _fsync_dir(target.parent)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _read_checksums(root: Path) -> dict[str, str]:
    path = root / "checksums.sha256"
    if not path.is_file() or path.is_symlink():
        raise BundleError("checksum file is missing")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "  " not in line:
            raise BundleError("checksum line is malformed")
        digest, relative = line.split("  ", 1)
        if not _SHA256.fullmatch(digest) or relative in result:
            raise BundleError("checksum entry is malformed or duplicated")
        result[_relative(relative)] = digest
    return result


def verify_bundle(output: Path | str) -> dict[str, Any]:
    """Verify every file/checksum and derive the literal v3 verdict."""

    root = Path(output)
    if not root.is_dir() or root.is_symlink():
        raise BundleError("bundle root is missing or symlinked")
    checksums = _read_checksums(root)
    actual: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_dir():
            if path.is_symlink():
                raise BundleError("bundle contains a symlinked directory")
            continue
        if path.is_symlink() or not path.is_file():
            raise BundleError("bundle contains a symlink or non-file")
        relative = path.relative_to(root).as_posix()
        if relative != "checksums.sha256":
            data = path.read_bytes()
            _safe_bytes(data, location=relative)
            actual[relative] = hashlib.sha256(data).hexdigest()
    if set(actual) != set(checksums):
        raise BundleError("checksums do not cover exactly every other file")
    if actual != checksums:
        raise BundleError("bundle checksum mismatch")
    manifest = _load_json(root / "manifest.json")
    _safe_json(manifest, location="manifest.json")
    if manifest.get("schema") != SCHEMA:
        raise BundleError("invalid acceptance manifest schema")
    if (
        manifest.get("competitive_baseline") != "not_applicable"
        or manifest.get("competitive_pair") != "not_applicable"
    ):
        raise BundleError("competitive sections are not explicit not_applicable")
    sections = manifest.get("sections")
    if not isinstance(sections, Mapping) or set(sections) != {
        "artifact",
        "claim_support",
        "synthesis",
        "scoring",
    }:
        raise BundleError("manifest sections are incomplete")
    declared_files = manifest.get("files")
    if not isinstance(declared_files, list) or declared_files != sorted(checksums) + [
        "checksums.sha256"
    ]:
        raise BundleError("manifest file declaration is not checksum-closed")
    gates_doc = _load_json(root / "gates.json")
    _safe_json(gates_doc, location="gates.json")
    gates = evaluate_gates(gates_doc.get("gates", gates_doc))
    score = _load_json(root / "score.json")
    _safe_json(score, location="score.json")
    claim_support = _load_json(root / "claim-support.json")
    _safe_json(claim_support, location="claim-support.json")
    recovery = _load_json(root / "recovery-evidence.json")
    _safe_json(recovery, location="recovery-evidence.json")
    for relative in checksums:
        if relative.startswith("artifacts/") and relative.endswith(".json"):
            parsed = _load_json(root / relative)
            _safe_json(parsed, location=relative)
    status = manifest.get("status")
    _validate_terminal_documents(
        status=status,
        sections=sections,
        score=score,
        claim_support=claim_support,
    )
    _validate_recovery(recovery)
    evidence_result: dict[str, Any] | None = None
    if status == "completed":
        sources = manifest.get("sources")
        if not isinstance(sources, list):
            raise BundleError("completed bundle requires manifest source rows")
        required_requirements = manifest.get(
            "required_requirements", _COMPLETED_REQUIRED_REQUIREMENTS
        )
        covered_requirements = manifest.get(
            "covered_requirements", _COMPLETED_REQUIRED_REQUIREMENTS
        )
        if (
            required_requirements != _COMPLETED_REQUIRED_REQUIREMENTS
            or covered_requirements != _COMPLETED_REQUIRED_REQUIREMENTS
        ):
            raise BundleError("completed bundle must cover all 15 requirements")
        evidence_result = minimum_evidence(
            sources,
            required_requirements=required_requirements,
            covered_requirements=covered_requirements,
            min_urls=_COMPLETED_MIN_URLS,
            min_domains=_COMPLETED_MIN_DOMAINS,
            min_primary=_COMPLETED_MIN_PRIMARY,
        )
        requirement_ids, citation_urls = _manifest_claim_bindings(manifest, sources)
        validate_claim_support(
            claim_support,
            required_requirements=required_requirements,
            source_hashes=set(evidence_result["source_text_sha256"]),
            requirement_ids=requirement_ids,
            citation_urls=citation_urls,
        )
    else:
        validate_claim_support(claim_support)
    _validate_section_locators(
        sections,
        {relative: b"" for relative in checksums},
        status=status,
    )
    if status in {"completed", "evaluator_not_run"}:
        files = {relative: b"" for relative in checksums}
        for name, gate in gates["gates"].items():
            if any(not _locator_exists(locator, files) for locator in gate["evidence"]):
                raise BundleError(f"gate {name} evidence locator does not exist")
    verdict = derive_verdict(
        status=status,
        gates_verdict=gates["verdict"],
        score=score,
        claim_support=claim_support,
        artifact_complete=sections["artifact"] != "not_run",
    )
    return {
        "schema": SCHEMA,
        "status": status,
        "verdict": verdict,
        "gates": gates,
        "score": score,
        "score_total": None
        if score.get("status") == "not_run"
        else calculate_score(score),
        "checksums_sha256": hashlib.sha256(
            (root / "checksums.sha256").read_bytes()
        ).hexdigest(),
    }
