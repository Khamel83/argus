"""Offline validation for the runtime identity baked into production images."""

import json
import re
from pathlib import Path
from typing import Any


class RuntimeManifestError(ValueError):
    """Raised when a runtime image cannot prove its required identity."""


_REQUIRED_CAPABILITIES = {
    "http_api",
    "mcp",
    "trafilatura",
    "playwright_browser",
    "crawl4ai",
    "obscura",
}
_LOCK_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def admit_runtime_manifest(path: Path | str, *, package_version: str) -> dict[str, Any]:
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
    if not isinstance(manifest.get("source_revision"), str) or not manifest["source_revision"].strip():
        raise RuntimeManifestError("runtime manifest is missing source revision")
    if manifest.get("package_version") != package_version:
        raise RuntimeManifestError(
            "runtime manifest package version does not match installed package version"
        )
    if not _LOCK_SHA256.fullmatch(str(manifest.get("lock_sha256", ""))):
        raise RuntimeManifestError("runtime manifest has an invalid lock identity")

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
    missing = sorted(_REQUIRED_CAPABILITIES.difference(capabilities))
    if missing:
        raise RuntimeManifestError(
            f"runtime manifest is missing required capabilities: {', '.join(missing)}"
        )
    if any(not isinstance(capabilities[name], bool) for name in _REQUIRED_CAPABILITIES):
        raise RuntimeManifestError("runtime manifest capabilities must be booleans")

    return manifest
