"""Separate, checksummed ``argus-acceptance-v3/free-targeted`` bundles.

This module is intentionally independent from :mod:`argus.scorecard.bundle`.
The v2 stability bundle has a different 22-gate contract and must retain its
existing semantics and bytes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any
from urllib.parse import urlsplit

from .contract import canonical_bytes, canonical_hash


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
FROZEN_GATE_DEFINITIONS_SHA256 = canonical_hash(FROZEN_GATE_DEFINITIONS)
FROZEN_RUBRIC_SHA256 = canonical_hash(dict(RUBRIC_CELLS))
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SENSITIVE_TEXT = re.compile(
    r"(?:\bbearer\s+|\bbasic\s+|-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:password|secret|api[_-]?key|cookie|authorization)\s*[:=])",
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
    r"(?:^|[\s'(\[])(?:/(?:Users|Volumes|private|tmp|var|opt|srv|etc|root|usr|Applications|Library|System|bin|sbin|proc|data|workspace|mnt|run|media)/|[A-Za-z]:[\\/]|\\\\)",
    re.IGNORECASE,
)


def _safe_json(value: Any, *, location: str = "document") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise BundleError(f"non-string key at {location}")
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in {
                "authorization",
                "bearer",
                "credential",
                "password",
                "secret",
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
    elif isinstance(value, str) and (
        _SENSITIVE_TEXT.search(value) or _ABSOLUTE_PATH.search(value)
    ):
        raise BundleError(f"sensitive/local value at {location}")


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
    verdict = "PASS" if all(status == "PASS" for status in statuses) else "FAIL"
    return {
        "schema": "argus-acceptance-v3/gates",
        "definitions_sha256": FROZEN_GATE_DEFINITIONS_SHA256,
        "gates": normalized,
        "verdict": verdict,
        "passed": statuses.count("PASS"),
    }


def calculate_score(score: Mapping[str, Any]) -> int:
    """Recompute the six-cell frozen score; ``not_run`` has no numeric zero."""

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
    for source in sources:
        if source.get("disposition") != "usable":
            continue
        url = source.get("url")
        if not isinstance(url, str) or not url.startswith("https://") or url in seen:
            continue
        parsed = urlsplit(url)
        if parsed.username or parsed.password or not parsed.hostname:
            continue
        required = {"provider", "extractor", "egress", "machine", "source_type"}
        if not required.issubset(source) or any(
            not source.get(key) for key in required
        ):
            raise BundleError("usable source lacks complete provenance")
        accepted.append(source)
        seen.add(url)
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
    }
    if (
        len(accepted) < min_urls
        or len(domains) < min_domains
        or primary < min_primary
        or covered_requirements < required_requirements
    ):
        raise BundleError("minimum evidence floor failed")
    return result


def validate_claim_support(
    value: Mapping[str, Any], *, required_requirements: int = 15
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
    for row in rows:
        if not isinstance(row, Mapping):
            raise BundleError("claim support row is invalid")
        identifier = row.get("requirement_id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise BundleError("claim support requirement IDs must be unique")
        seen.add(identifier)
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
        citation_url = row.get("citation_url")
        if not isinstance(citation_url, str) or not citation_url.startswith("https://"):
            raise BundleError("claim support citation URL is required")
        evaluator = row.get("evaluator")
        if (
            not isinstance(evaluator, Mapping)
            or evaluator.get("model") != "gpt-5.6-sol"
            or not _SHA256.fullmatch(str(evaluator.get("run_receipt_sha256", "")))
        ):
            raise BundleError("claim support evaluator identity is invalid")


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
        from datetime import datetime, timezone

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
    if status == "evaluator_not_run":
        if (
            sections["artifact"] == "not_run"
            or sections["claim_support"] != "not_run"
            or sections["scoring"] != "not_run"
        ):
            raise BundleError(
                "evaluator_not_run must retain completed artifact and not_run evaluation"
            )
    elif status != "completed" and any(
        value != "not_run" for value in sections.values()
    ):
        raise BundleError("pre-artifact terminal branch cannot fabricate sections")
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
    validate_claim_support(payload["claim_support"])
    _safe_json(manifest)
    _safe_json(gates)
    _safe_json(score_value)
    _safe_json(payload["claim_support"])
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
            # JSON-looking artifacts are parsed and scanned; binary pages remain
            # bounded bytes and are covered by the checksum below.
            if relative.endswith(".json"):
                try:
                    parsed = json.loads(value.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BundleError(
                        f"artifact {relative} is not strict JSON"
                    ) from exc
                _safe_json(parsed, location=relative)
            files[relative] = value
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
            actual[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if set(actual) != set(checksums):
        raise BundleError("checksums do not cover exactly every other file")
    if actual != checksums:
        raise BundleError("bundle checksum mismatch")
    manifest = _load_json(root / "manifest.json")
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
    gates = evaluate_gates(gates_doc.get("gates", gates_doc))
    score = _load_json(root / "score.json")
    claim_support = _load_json(root / "claim-support.json")
    validate_claim_support(claim_support)
    status = manifest.get("status")
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
