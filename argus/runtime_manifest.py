"""Offline validation for the runtime identity baked into production images."""

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


class RuntimeManifestError(ValueError):
    """Raised when a runtime image cannot prove its required identity."""


EXPECTED_RUNTIME_CAPABILITIES = {
    "http_api": True,
    "mcp": True,
    "trafilatura": True,
    "playwright_browser": True,
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


def installed_playwright_browser_contract() -> dict[str, str]:
    """Return the browser identity required by the installed Playwright wheel."""
    spec = importlib.util.find_spec("playwright")
    if spec is None or spec.origin is None:
        raise RuntimeManifestError("Playwright package is missing")
    browser_registry = (
        Path(spec.origin).parent / "driver" / "package" / "browsers.json"
    )
    try:
        registry = json.loads(browser_registry.read_text(encoding="utf-8"))
        headless_shell = next(
            browser
            for browser in registry["browsers"]
            if browser["name"] == "chromium-headless-shell"
        )
        version = importlib.metadata.version("playwright")
        revision = str(headless_shell["revision"])
        browser_version = str(headless_shell["browserVersion"])
    except (OSError, KeyError, StopIteration, json.JSONDecodeError) as error:
        raise RuntimeManifestError(
            "installed Playwright browser contract is unreadable"
        ) from error
    return {
        "playwright_version": version,
        "revision": revision,
        "browser_version": browser_version,
    }


def _find_headless_shell(browser_root: Path, revision: str) -> Path | None:
    revision_root = browser_root / f"chromium_headless_shell-{revision}"
    if not revision_root.is_dir():
        return None
    for executable_name in ("chrome-headless-shell", "headless_shell"):
        for candidate in revision_root.rglob(executable_name):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def playwright_browser_artifact(
    browser_root: Path | str,
    revision: str,
) -> tuple[Path, str]:
    """Resolve and hash the exact Playwright headless-shell artifact."""
    executable = _find_headless_shell(Path(browser_root), revision)
    if executable is None:
        raise RuntimeManifestError(
            "Playwright-matched Chromium headless shell is missing or not executable"
        )
    return executable, hashlib.sha256(executable.read_bytes()).hexdigest()


def _validate_browser_contract(browser: object) -> Path:
    if not isinstance(browser, dict):
        raise RuntimeManifestError("runtime manifest is missing browser contract")
    installed = installed_playwright_browser_contract()
    declared = {
        key: browser.get(key)
        for key in ("playwright_version", "revision", "browser_version")
    }
    if declared != installed:
        raise RuntimeManifestError(
            "runtime manifest browser contract does not match installed Playwright"
        )
    browser_root = browser.get("browser_root")
    if not isinstance(browser_root, str) or not Path(browser_root).is_absolute():
        raise RuntimeManifestError("runtime manifest has an invalid browser root")
    executable, actual_digest = playwright_browser_artifact(
        browser_root,
        installed["revision"],
    )
    declared_digest = browser.get("executable_sha256")
    if (
        not isinstance(declared_digest, str)
        or not _LOCK_SHA256.fullmatch(declared_digest)
        or declared_digest != actual_digest
    ):
        raise RuntimeManifestError("Chromium headless shell identity does not match manifest")
    try:
        version_result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeManifestError(
            "Chromium headless shell version probe failed"
        ) from error
    version_output = f"{version_result.stdout}\n{version_result.stderr}"
    if (
        version_result.returncode != 0
        or installed["browser_version"] not in version_output
    ):
        raise RuntimeManifestError(
            "Chromium headless shell version does not match Playwright contract"
        )
    return executable


def inspect_playwright_browser_capability(
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Return sanitized runtime browser evidence for authenticated status."""
    manifest_path = Path(
        path or os.environ.get("ARGUS_RUNTIME_MANIFEST", "/app/runtime-manifest.json")
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {
            "declared": False,
            "available": False,
            "sandbox_required": True,
            "degraded_reason": "runtime_manifest_unavailable",
        }

    capabilities = manifest.get("capabilities", {})
    declared = (
        isinstance(capabilities, dict)
        and capabilities.get("playwright_browser") is True
    )
    if not declared:
        return {
            "declared": False,
            "available": False,
            "sandbox_required": True,
        }
    try:
        executable = _validate_browser_contract(manifest.get("browser"))
        browser = manifest["browser"]
        return {
            "declared": True,
            "available": True,
            "sandbox_required": True,
            "playwright_version": browser["playwright_version"],
            "revision": browser["revision"],
            "browser_version": browser["browser_version"],
            "executable": executable.name,
        }
    except (OSError, RuntimeManifestError, TypeError):
        return {
            "declared": True,
            "available": False,
            "sandbox_required": True,
            "degraded_reason": "browser_artifact_unavailable",
        }


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

    _validate_browser_contract(manifest.get("browser"))

    return manifest
