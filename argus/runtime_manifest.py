"""Offline validation for the runtime identity baked into production images."""

import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any


class RuntimeManifestError(ValueError):
    """Raised when a runtime image cannot prove its required identity."""


EXPECTED_RUNTIME_CAPABILITIES = {
    "http_api": True,
    "mcp": True,
    "trafilatura": True,
    "playwright_browser": False,
    "crawl4ai": False,
    "obscura": False,
}
_CAPABILITY_MODULES = {
    "http_api": "fastapi",
    "mcp": "mcp",
    "trafilatura": "trafilatura",
}
_LOCK_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FULL_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def is_production_source_revision(source_revision: object) -> bool:
    """Return whether a revision is a canonical full Git commit identity."""
    return (
        isinstance(source_revision, str)
        and _FULL_COMMIT_SHA.fullmatch(source_revision) is not None
    )


def admit_runtime_manifest(
    path: Path | str,
    *,
    package_version: str,
    allow_development_revision: bool = False,
) -> dict[str, Any]:
    """Read and validate a baked manifest without contacting any external service."""
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise RuntimeManifestError(f"runtime manifest is missing: {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeManifestError(f"runtime manifest is unreadable: {manifest_path}") from error

    if not isinstance(manifest, dict):
        raise RuntimeManifestError("runtime manifest must contain a JSON object")
    source_revision = manifest.get("source_revision")
    if not isinstance(source_revision, str) or not source_revision.strip():
        raise RuntimeManifestError("runtime manifest is missing source revision")
    if (
        not is_production_source_revision(source_revision)
        and not allow_development_revision
    ):
        raise RuntimeManifestError(
            "runtime manifest source revision must be a full commit SHA "
            "for production admission"
        )
    if manifest.get("package_version") != package_version:
        raise RuntimeManifestError(
            "runtime manifest package version does not match installed package version"
        )
    lock_digest = str(manifest.get("lock_sha256", ""))
    if not _LOCK_SHA256.fullmatch(lock_digest):
        raise RuntimeManifestError("runtime manifest has an invalid lock identity")
    lock_file = manifest.get("lock_file")
    if not isinstance(lock_file, str) or not lock_file:
        raise RuntimeManifestError("runtime manifest is missing baked lock artifact")
    relative_lock = Path(lock_file)
    if relative_lock.is_absolute() or ".." in relative_lock.parts:
        raise RuntimeManifestError("runtime manifest has an unsafe baked lock path")
    lock_path = manifest_path.parent / relative_lock
    if not lock_path.is_file():
        raise RuntimeManifestError(f"baked lock artifact is missing: {lock_path}")
    actual_lock_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    if actual_lock_digest != lock_digest:
        raise RuntimeManifestError("baked lock artifact does not match lock identity")

    schema = manifest.get("schema")
    if not isinstance(schema, dict) or not all(
        isinstance(schema.get(key), int) for key in ("minimum", "maximum")
    ):
        raise RuntimeManifestError("runtime manifest is missing supported schema range")
    if schema["minimum"] > schema["maximum"]:
        raise RuntimeManifestError("runtime manifest has an invalid supported schema range")

    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, dict):
        raise RuntimeManifestError("runtime manifest is missing runtime capabilities")
    if capabilities != EXPECTED_RUNTIME_CAPABILITIES:
        raise RuntimeManifestError("runtime manifest capabilities do not match image contract")
    missing_modules = sorted(
        capability
        for capability, module in _CAPABILITY_MODULES.items()
        if capabilities[capability] and importlib.util.find_spec(module) is None
    )
    if missing_modules:
        raise RuntimeManifestError(
            "runtime capability dependencies are missing: "
            + ", ".join(missing_modules)
        )

    return manifest
