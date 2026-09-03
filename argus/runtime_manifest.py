"""Offline validation for the runtime identity baked into production images."""

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from argus.contracts.identity import SchemaIdentity


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
RUNTIME_MANIFEST_VERSION = 2
SCHEMA_CONTRACT_FORMAT = "argus-schema-contract-v1"
SCHEMA_IDENTITY_SCHEMA = "argus.schema-identity.v1"
_SCHEMA_IDENTITY_FIELDS = (
    "schema_head",
    "migration_chain_sha256",
    "canonical_postgresql_schema_sha256",
    "schema_contract_format",
)
_CAPABILITY_MODULES = {
    "http_api": "fastapi",
    "mcp": "mcp",
    "trafilatura": "trafilatura",
}
_LOCK_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FULL_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE_RELATIVE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a release artifact with one deterministic JSON representation."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise RuntimeManifestError("value is not canonical JSON") from error


def sha256_bytes(value: bytes) -> str:
    """Return a lowercase SHA-256 digest for immutable release evidence."""
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path | str) -> str:
    """Hash one release artifact without exposing its contents."""
    try:
        return sha256_bytes(Path(path).read_bytes())
    except OSError as error:
        raise RuntimeManifestError(f"release artifact is unreadable: {path}") from error


def _safe_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_RELATIVE_PATH.fullmatch(value):
        raise RuntimeManifestError(f"runtime manifest has an invalid {label}")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeManifestError(f"runtime manifest has an unsafe {label}")
    return value


def _artifact_path(manifest_path: Path, value: object, label: str) -> Path:
    relative = _safe_relative_path(value, label)
    path = manifest_path.parent / relative
    try:
        path.resolve(strict=False).relative_to(manifest_path.parent.resolve(strict=False))
    except ValueError as error:
        raise RuntimeManifestError(f"runtime manifest has an unsafe {label}") from error
    return path


def migration_chain_sha256(
    schema_head: str,
    *,
    migrations_path: Path | str | None = None,
) -> str:
    """Hash the ordered source migration chain used by the schema contract.

    Recovery owns the canonical implementation when it is available.  The
    local parser is retained for build-time compatibility with a source tree
    before the recovery identity module is installed.
    """
    root = Path(
        migrations_path
        or Path(__file__).resolve().parents[1] / "migrations" / "versions"
    )
    try:
        from argus.recovery.database import migration_chain_sha256 as authoritative
    except ImportError:
        authoritative = None
    if authoritative is not None:
        try:
            return authoritative(schema_head, migrations_path=root)
        except TypeError:
            return authoritative(schema_head)
    if not root.is_dir():
        raise RuntimeManifestError("migration directory is unavailable")
    # This path is used only by an older source tree.  The recovery module is
    # the authoritative implementation once schema identity support exists.
    contents = b"".join(
        path.name.encode("utf-8") + b"\0" + path.read_bytes()
        for path in sorted(root.glob("*.py"))
        if path.name != "__init__.py"
    )
    return sha256_bytes(schema_head.encode("utf-8") + b"\0" + contents)


def _catalog_hash(contract: Mapping[str, Any]) -> str:
    """Hash the canonical PostgreSQL catalog projection in a contract file."""
    try:
        from argus.recovery.database import (
            canonical_postgresql_schema_sha256 as authoritative,
        )
    except ImportError:
        authoritative = None
    if authoritative is not None:
        try:
            return authoritative(dict(contract))
        except (TypeError, ValueError):
            pass
    required = ("tables", "columns", "constraints", "indexes")
    if any(key not in contract for key in required):
        raise RuntimeManifestError("schema contract has no PostgreSQL catalog")
    catalog = {
        "tables": sorted(str(table) for table in contract["tables"]),
        "columns": contract["columns"],
        "constraints": dict(sorted(contract["constraints"].items())),
        "indexes": dict(sorted(contract["indexes"].items())),
        "functions": dict(sorted((contract.get("functions") or {}).items())),
    }
    return sha256_bytes(canonical_json_bytes(catalog))


def schema_identity_from_contract(
    path: Path | str,
    *,
    expected_head: str | None = None,
) -> dict[str, str]:
    """Read and authenticate the exact four-field production schema identity."""
    contract_path = Path(path)
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeManifestError("schema contract is unreadable") from error
    if not isinstance(contract, dict):
        raise RuntimeManifestError("schema contract must be a JSON object")
    declared_contract_hash = contract.get("contract_sha256")
    if declared_contract_hash is not None:
        unsigned_contract = dict(contract)
        unsigned_contract.pop("contract_sha256", None)
        if declared_contract_hash != sha256_bytes(
            canonical_json_bytes(unsigned_contract)
        ):
            raise RuntimeManifestError("schema contract hash is invalid")
    schema_head = contract.get("schema_head")
    if expected_head is not None and schema_head != expected_head:
        raise RuntimeManifestError("schema contract head does not match manifest")
    if not isinstance(schema_head, str) or not schema_head:
        raise RuntimeManifestError("schema contract is missing schema head")
    declared = {field: contract.get(field) for field in _SCHEMA_IDENTITY_FIELDS}
    migrations_root = contract_path.parents[2] / "migrations" / "versions"
    has_migrations_root = migrations_root.is_dir()
    if all(isinstance(value, str) for value in declared.values()):
        identity = SchemaIdentity(**declared)
        if has_migrations_root and identity.migration_chain_sha256 != migration_chain_sha256(
            schema_head,
            migrations_path=migrations_root,
        ):
            raise RuntimeManifestError("schema contract migration identity is stale")
        if set(("tables", "columns", "constraints", "indexes")) <= set(contract) and (
            identity.canonical_postgresql_schema_sha256 != _catalog_hash(contract)
        ):
            raise RuntimeManifestError("schema contract catalog identity is stale")
    else:
        # Legacy checked-in contracts are accepted only as build inputs.  The
        # generated manifest still receives the complete four-field identity.
        identity = SchemaIdentity(
            schema_head=schema_head,
            migration_chain_sha256=migration_chain_sha256(
                schema_head,
                migrations_path=migrations_root if has_migrations_root else None,
            ),
            canonical_postgresql_schema_sha256=_catalog_hash(contract),
            schema_contract_format=SCHEMA_CONTRACT_FORMAT,
        )
    declared_id = contract.get("schema_id")
    if declared_id is not None and declared_id != identity.identity_id:
        raise RuntimeManifestError("schema contract schema identity hash is invalid")
    return {**identity.as_dict(), "schema_id": identity.identity_id}


def _validate_schema_identity(manifest: Mapping[str, Any], manifest_path: Path) -> dict[str, str]:
    value = manifest.get("schema_identity")
    if not isinstance(value, Mapping):
        raise RuntimeManifestError("runtime manifest is missing schema identity")
    if set(value) != set(_SCHEMA_IDENTITY_FIELDS):
        raise RuntimeManifestError("runtime manifest schema identity fields are incomplete")
    try:
        identity = SchemaIdentity(**{field: value[field] for field in _SCHEMA_IDENTITY_FIELDS})
    except (TypeError, ValueError) as error:
        raise RuntimeManifestError("runtime manifest schema identity is invalid") from error
    if identity.schema_contract_format != SCHEMA_CONTRACT_FORMAT:
        raise RuntimeManifestError("runtime manifest schema contract format is unsupported")
    declared_id = manifest.get("schema_id")
    if declared_id != identity.identity_id:
        raise RuntimeManifestError("runtime manifest schema identity hash is invalid")
    for field in _SCHEMA_IDENTITY_FIELDS:
        if manifest.get(field) != value[field]:
            raise RuntimeManifestError(
                "runtime manifest schema identity aliases do not match"
            )
    contract_path = _artifact_path(
        manifest_path,
        manifest.get("schema_contract_file"),
        "schema contract path",
    )
    declared_contract_digest = manifest.get("schema_contract_sha256")
    if not isinstance(declared_contract_digest, str) or not _LOCK_SHA256.fullmatch(
        declared_contract_digest
    ):
        raise RuntimeManifestError("runtime manifest has an invalid schema contract identity")
    if not contract_path.is_file() or sha256_file(contract_path) != declared_contract_digest:
        raise RuntimeManifestError("schema contract does not match runtime manifest")
    contract_identity = schema_identity_from_contract(
        contract_path,
        expected_head=identity.schema_head,
    )
    if any(contract_identity[field] != value[field] for field in _SCHEMA_IDENTITY_FIELDS):
        raise RuntimeManifestError("runtime manifest schema identity does not match contract")
    descriptor_path = _artifact_path(
        manifest_path,
        manifest.get("release_descriptor_file"),
        "release descriptor path",
    )
    descriptor_digest = manifest.get("release_descriptor_digest")
    if not isinstance(descriptor_digest, str) or not _LOCK_SHA256.fullmatch(descriptor_digest):
        raise RuntimeManifestError("runtime manifest has an invalid release descriptor identity")
    if not descriptor_path.is_file() or sha256_file(descriptor_path) != descriptor_digest:
        raise RuntimeManifestError("release descriptor does not match runtime manifest")
    return contract_identity


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
    manifest_version = manifest.get("manifest_version", 1)
    if not isinstance(manifest_version, int) or manifest_version < 1:
        raise RuntimeManifestError("runtime manifest has an invalid manifest version")
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

    if manifest_version >= RUNTIME_MANIFEST_VERSION:
        _validate_schema_identity(manifest, manifest_path)

    _validate_browser_contract(manifest.get("browser"))

    return manifest
