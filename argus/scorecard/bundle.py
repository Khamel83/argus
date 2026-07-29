"""Secret-free, checksummed scorecard bundles.

Bundles are immutable diagnostic records.  They intentionally contain no
transport credentials, provider-native payloads, or execution authorization.
"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .stability import StabilityVerdict


class BundleError(ValueError):
    """A bundle is incomplete, inconsistent, or unsafe to publish."""


SCHEMA_VERSION = "scorecard-bundle-v1"
_FORBIDDEN_KEY = re.compile(
    r"(?:authorization|credential|password|secret|api[_-]?key|token|raw[_-]?payload|native[_-]?payload)",
    re.IGNORECASE,
)
_FORBIDDEN_VALUE = re.compile(
    r"(?:bearer\s+|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9_]+|\bsk-[A-Za-z0-9_-]+|__[A-Z0-9_]*SECRET[A-Z0-9_]*__)",
    re.IGNORECASE,
)


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _hash_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _safe(value: Any, location: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _FORBIDDEN_KEY.search(str(key)):
                raise BundleError(f"forbidden sensitive field at {location}.{key}")
            _safe(nested, f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _safe(nested, f"{location}[{index}]")
    elif isinstance(value, str) and _FORBIDDEN_VALUE.search(value):
        raise BundleError(f"forbidden sensitive value at {location}")


def _write_json(root: Path, relative: str, value: Any) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _canonical_bytes(value)
    path.write_bytes(encoded)
    return _hash_bytes(encoded)


def _required_paths(lane: str) -> tuple[str, ...]:
    base = (
        "manifest.json",
        "identities/candidate.json",
        "corpus/manifest.json",
        "stability/gates.json",
        "stability/surface-equivalence.json",
        "artifacts/hermetic-summary.json",
        "checksums.sha256",
    )
    if lane == "competitive":
        return base + (
            "identities/baseline.json",
            "competitive/deterministic-metrics.json",
            "competitive/blinded-comparisons.json",
            "competitive/verdict.json",
        )
    if lane != "hermetic":
        raise BundleError(f"unsupported scorecard lane: {lane}")
    return base


def _identity(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value.get("generation"), str) or not value["generation"]:
        raise BundleError(f"{label} identity must declare generation")
    return dict(value)


def write_bundle(
    output: Path,
    *,
    lane: str,
    stability: StabilityVerdict,
    payload: Mapping[str, Any],
) -> Path:
    """Write one deterministic bundle and return its root directory."""
    _safe(payload)
    if output.exists():
        raise BundleError(f"bundle output already exists: {output}")
    output.mkdir(parents=True)
    _required_paths(lane)
    candidate = _identity(payload.get("candidate_identity", {}), "candidate")
    generation = payload.get("generation")
    if candidate["generation"] != generation:
        raise BundleError(
            "candidate identity generation does not match bundle generation"
        )
    baseline = payload.get("baseline_identity")
    if lane == "competitive":
        baseline = _identity(baseline or {}, "baseline")
        if baseline["generation"] != generation:
            raise BundleError("baseline and candidate generations must match")

    files: dict[str, str] = {}
    files["identities/candidate.json"] = _write_json(
        output, "identities/candidate.json", candidate
    )
    if baseline is not None:
        files["identities/baseline.json"] = _write_json(
            output, "identities/baseline.json", baseline
        )
    corpus = payload.get("corpus")
    if not isinstance(corpus, Mapping) or not corpus:
        raise BundleError("bundle requires corpus identity")
    files["corpus/manifest.json"] = _write_json(output, "corpus/manifest.json", corpus)
    files["stability/gates.json"] = _write_json(
        output, "stability/gates.json", asdict(stability)
    )
    surface = payload.get("surface_equivalence")
    if not isinstance(surface, Mapping) or not surface:
        raise BundleError("bundle requires surface equivalence evidence")
    files["stability/surface-equivalence.json"] = _write_json(
        output, "stability/surface-equivalence.json", surface
    )
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, Mapping) or not artifacts:
        raise BundleError("bundle requires diagnostic artifacts")
    for relative, artifact in artifacts.items():
        relative = str(relative)
        if "/" in relative or relative in {"", ".", ".."}:
            raise BundleError("artifact paths must be simple relative filenames")
        files[f"artifacts/{relative}"] = _write_json(
            output, f"artifacts/{relative}", artifact
        )
    if "artifacts/hermetic-summary.json" not in files:
        raise BundleError("bundle requires artifacts/hermetic-summary.json")
    if lane == "competitive":
        competitive = payload.get("competitive")
        if not isinstance(competitive, Mapping):
            raise BundleError("competitive lane requires competitive evidence")
        for name in ("deterministic_metrics", "blinded_comparisons", "verdict"):
            if name not in competitive:
                raise BundleError(f"competitive lane missing {name}")
        files["competitive/deterministic-metrics.json"] = _write_json(
            output,
            "competitive/deterministic-metrics.json",
            competitive["deterministic_metrics"],
        )
        files["competitive/blinded-comparisons.json"] = _write_json(
            output,
            "competitive/blinded-comparisons.json",
            competitive["blinded_comparisons"],
        )
        files["competitive/verdict.json"] = _write_json(
            output, "competitive/verdict.json", competitive["verdict"]
        )
    sections = {
        "identities": "required",
        "corpus": "required",
        "stability": "required",
        "artifacts": "required",
        "competitive": "required" if lane == "competitive" else "not_run",
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": payload.get("run_id"),
        "generation": generation,
        "lane": lane,
        "sections": sections,
        "identities": {
            "candidate": {
                "path": "identities/candidate.json",
                "sha256": files["identities/candidate.json"],
            }
        },
        "provider_eligibility_snapshot": payload.get("provider_snapshot", {}),
        "stability_verdict": stability.verdict,
        "competitive_verdict": "not_run"
        if lane == "hermetic"
        else payload["competitive"]["verdict"],
        "files": files,
    }
    if baseline is not None:
        manifest["identities"]["baseline"] = {
            "path": "identities/baseline.json",
            "sha256": files["identities/baseline.json"],
        }
    _write_json(output, "manifest.json", manifest)
    checksummed = sorted(path for path in files if path != "manifest.json") + [
        "manifest.json"
    ]
    checksum_lines = [
        f"{_hash_bytes((output / path).read_bytes())}  {path}" for path in checksummed
    ]
    (output / "checksums.sha256").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )
    verify_bundle(output)
    return output


def verify_bundle(root: Path) -> dict[str, Any]:
    """Validate declared sections, identities, checksums, and publication safety."""
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise BundleError("bundle missing required manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BundleError("bundle manifest is invalid JSON") from exc
    _safe(manifest)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise BundleError("unsupported bundle schema")
    lane = manifest.get("lane")
    required = _required_paths(lane)
    for relative in required:
        if not (root / relative).is_file():
            raise BundleError(f"bundle missing required section: {relative}")
    checksum_path = root / "checksums.sha256"
    checksums: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as exc:
            raise BundleError("invalid checksum entry") from exc
        checksums[relative] = digest
    for relative, expected in checksums.items():
        path = root / relative
        if not path.is_file() or _hash_bytes(path.read_bytes()) != expected:
            raise BundleError(f"checksum mismatch: {relative}")
        if relative.endswith(".json"):
            _safe(json.loads(path.read_text(encoding="utf-8")), relative)
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise BundleError("manifest is missing file hashes")
    for relative, expected in files.items():
        path = root / str(relative)
        if not path.is_file() or _hash_bytes(path.read_bytes()) != expected:
            raise BundleError(f"checksum mismatch: {relative}")
    identities = manifest.get("identities")
    if not isinstance(identities, Mapping) or "candidate" not in identities:
        raise BundleError("manifest missing candidate identity")
    generation = manifest.get("generation")
    for label, declaration in identities.items():
        if not isinstance(declaration, Mapping):
            raise BundleError("invalid identity declaration")
        path = root / str(declaration.get("path", ""))
        if not path.is_file() or _hash_bytes(path.read_bytes()) != declaration.get(
            "sha256"
        ):
            raise BundleError(f"identity checksum mismatch: {label}")
        identity = json.loads(path.read_text(encoding="utf-8"))
        if identity.get("generation") != generation:
            raise BundleError("cross-generation identity comparison")
    return manifest
