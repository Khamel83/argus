"""Closed receipt for two consecutive, genuinely inconclusive free runs."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from .bundle import BundleError, verify_bundle


class ResidualError(ValueError):
    """A bounded-inconclusive residual is incomplete or cannot be derived."""


_RECEIPT_FIELDS = {
    "schema",
    "status",
    "reason",
    "can_authorize_deployment",
    "generation",
    "dimensions_sha256",
    "candidate_identity",
    "baseline_identity",
    "attempts",
}
_ATTEMPT_FIELDS = {
    "ordinal",
    "run_id",
    "manifest_sha256",
    "checksums_sha256",
    "verdict",
}
_IDENTITY_FIELDS = {"commit", "image_digest", "generation"}


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ResidualError(f"residual is not canonical JSON: {exc}") from exc


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResidualError(f"{label} is invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise ResidualError(f"{label} must be an object")
    return value


def _source(bundle: Path, ordinal: int) -> dict[str, Any]:
    try:
        manifest = verify_bundle(bundle)
    except BundleError as exc:
        raise ResidualError(
            f"attempt {ordinal} is not a verified bundle: {exc}"
        ) from exc
    if (
        manifest["lane"] != "competitive"
        or manifest["dimensions"]["profile"] != "free"
        or manifest["stability_verdict"] != "stable"
        or manifest["competitive_verdict"] != "inconclusive"
    ):
        raise ResidualError(
            "bounded residual requires stable inconclusive free competitive bundles"
        )
    candidate = _load(bundle / "identities" / "candidate.json", "candidate identity")
    baseline = _load(bundle / "identities" / "baseline.json", "baseline identity")
    return {
        "manifest": manifest,
        "candidate": candidate,
        "baseline": baseline,
        "attempt": {
            "ordinal": ordinal,
            "run_id": manifest["run_id"],
            "manifest_sha256": _hash(bundle / "manifest.json"),
            "checksums_sha256": _hash(bundle / "checksums.sha256"),
            "verdict": manifest["competitive_verdict"],
        },
    }


def _identity_projection(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "commit": identity.get("commit"),
        "image_digest": identity.get("image_digest"),
        "generation": identity.get("generation"),
    }


def _derive(first_bundle: Path, second_bundle: Path) -> dict[str, Any]:
    first = _source(first_bundle, 1)
    second = _source(second_bundle, 2)
    if first["attempt"]["run_id"] == second["attempt"]["run_id"]:
        raise ResidualError("bounded residual requires two distinct run ids")
    if first["manifest"]["generation"] != second["manifest"]["generation"]:
        raise ResidualError("bounded residual attempts must share one generation")
    if first["manifest"]["dimensions"] != second["manifest"]["dimensions"]:
        raise ResidualError("bounded residual dimensions must be immutable")
    for label in ("candidate", "baseline"):
        if _identity_projection(first[label]) != _identity_projection(second[label]):
            raise ResidualError(f"bounded residual {label} identity changed")
    first_finished = max(
        datetime.fromisoformat(first[label]["finished_at"].replace("Z", "+00:00"))
        for label in ("candidate", "baseline")
    )
    second_started = min(
        datetime.fromisoformat(second[label]["started_at"].replace("Z", "+00:00"))
        for label in ("candidate", "baseline")
    )
    if second_started < first_finished:
        raise ResidualError(
            "bounded residual attempts must be consecutive chronological executions"
        )
    dimensions = first["manifest"]["dimensions"]
    return {
        "schema": "scorecard-bounded-inconclusive-residual-v1",
        "status": "accepted_residual_risk",
        "reason": "two_consecutive_free_profile_inconclusive",
        "can_authorize_deployment": False,
        "generation": first["manifest"]["generation"],
        "dimensions_sha256": sha256(_canonical_bytes(dimensions)).hexdigest(),
        "candidate_identity": _identity_projection(first["candidate"]),
        "baseline_identity": _identity_projection(first["baseline"]),
        "attempts": [first["attempt"], second["attempt"]],
    }


def write_bounded_inconclusive_residual(
    output: Path, *, first_bundle: Path, second_bundle: Path
) -> Path:
    """Atomically write a closed residual-risk receipt from two verified bundles."""
    if output.exists() or output.is_symlink():
        raise ResidualError(f"residual output already exists: {output}")
    receipt = _derive(first_bundle, second_bundle)
    receipt_bytes = _canonical_bytes(receipt)
    checksums = f"{sha256(receipt_bytes).hexdigest()}  residual.json\n".encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent)
    )
    try:
        (staging / "residual.json").write_bytes(receipt_bytes)
        (staging / "checksums.sha256").write_bytes(checksums)
        verify_bounded_inconclusive_residual(
            staging, first_bundle=first_bundle, second_bundle=second_bundle
        )
        if output.exists() or output.is_symlink():
            raise ResidualError(f"residual output already exists: {output}")
        staging.replace(output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output


def verify_bounded_inconclusive_residual(
    root: Path, *, first_bundle: Path, second_bundle: Path
) -> dict[str, Any]:
    """Verify exact files, checksum, source bundles, and derived residual contents."""
    if not root.is_dir() or root.is_symlink():
        raise ResidualError("residual root must be a real directory")
    files: set[str] = set()
    for directory, directories, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        if any((directory_path / name).is_symlink() for name in directories):
            raise ResidualError("residual contains a symlink")
        for name in filenames:
            path = directory_path / name
            if path.is_symlink():
                raise ResidualError("residual contains a symlink")
            files.add(path.relative_to(root).as_posix())
    if files != {"residual.json", "checksums.sha256"}:
        raise ResidualError("residual file set is not closed")
    receipt = _load(root / "residual.json", "residual receipt")
    if set(receipt) != _RECEIPT_FIELDS:
        raise ResidualError("residual receipt must contain exact schema keys")
    attempts = receipt["attempts"]
    if (
        not isinstance(attempts, list)
        or len(attempts) != 2
        or any(
            not isinstance(attempt, Mapping) or set(attempt) != _ATTEMPT_FIELDS
            for attempt in attempts
        )
        or any(
            not isinstance(receipt[field], Mapping)
            or set(receipt[field]) != _IDENTITY_FIELDS
            for field in ("candidate_identity", "baseline_identity")
        )
    ):
        raise ResidualError("residual typed contents are invalid")
    expected_line = f"{_hash(root / 'residual.json')}  residual.json\n"
    if (root / "checksums.sha256").read_text(encoding="utf-8") != expected_line:
        raise ResidualError("residual checksum is invalid")
    expected = _derive(first_bundle, second_bundle)
    if receipt != expected:
        raise ResidualError("residual receipt is not derivable from source bundles")
    return dict(receipt)
