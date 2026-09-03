#!/usr/bin/env python3
"""Build the identity manifest copied into the production image."""

import argparse
import json
import tomllib
from pathlib import Path

from argus.runtime_manifest import (
    EXPECTED_RUNTIME_CAPABILITIES,
    RUNTIME_MANIFEST_VERSION,
    installed_playwright_browser_contract,
    playwright_browser_artifact,
    schema_identity_from_contract,
    sha256_file,
)


def _default_schema_contract() -> Path:
    """Resolve the checked-in contract for the current migration head."""
    try:
        from argus.recovery.database import (
            EXPECTED_SCHEMA_HEAD,
            SCHEMA_CONTRACT_PATHS,
        )

        return Path(SCHEMA_CONTRACT_PATHS[EXPECTED_SCHEMA_HEAD])
    except (ImportError, KeyError):
        candidates = sorted(Path("argus/recovery").glob("argus_schema_*.json"))
        if not candidates:
            raise RuntimeError("checked-in schema contract is unavailable")
        return candidates[-1]


def _manifest_artifact_path(path: Path, output: Path) -> str:
    """Return a safe path from the image root to a baked release artifact."""
    try:
        relative = path.resolve().relative_to(output.parent.resolve())
    except ValueError:
        relative = path
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"release artifact is outside image root: {path}")
    return relative.as_posix()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--lock-file", type=Path, default=Path("uv.lock"))
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--browser-root", type=Path, default=Path("/ms-playwright"))
    parser.add_argument("--release-descriptor", type=Path, default=Path("argus/mcp/release_descriptor.json"))
    parser.add_argument("--schema-contract", type=Path, default=None)
    args = parser.parse_args()

    package = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))["project"]
    schema_contract = args.schema_contract or _default_schema_contract()
    schema_identity = schema_identity_from_contract(schema_contract)
    browser_contract = installed_playwright_browser_contract()
    _, executable_sha256 = playwright_browser_artifact(
        args.browser_root,
        browser_contract["revision"],
    )
    manifest = {
        "manifest_version": RUNTIME_MANIFEST_VERSION,
        "source_revision": args.source_revision,
        "package_version": package["version"],
        "schema": {"minimum": 1, "maximum": 1},
        "lock_file": args.lock_file.name,
        "lock_sha256": sha256_file(args.lock_file),
        "capabilities": EXPECTED_RUNTIME_CAPABILITIES,
        "release_descriptor_file": _manifest_artifact_path(
            args.release_descriptor,
            args.output,
        ),
        "release_descriptor_digest": sha256_file(args.release_descriptor),
        "schema_contract_file": _manifest_artifact_path(schema_contract, args.output),
        "schema_contract_sha256": sha256_file(schema_contract),
        "schema_identity": {
            key: schema_identity[key]
            for key in (
                "schema_head",
                "migration_chain_sha256",
                "canonical_postgresql_schema_sha256",
                "schema_contract_format",
            )
        },
        **{
            key: schema_identity[key]
            for key in (
                "schema_head",
                "migration_chain_sha256",
                "canonical_postgresql_schema_sha256",
                "schema_contract_format",
            )
        },
        "schema_id": schema_identity["schema_id"],
        "browser": {
            **browser_contract,
            "browser_root": str(args.browser_root),
            "executable_sha256": executable_sha256,
        },
    }
    args.output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
