#!/usr/bin/env python3
"""Fail fast when reproducible release inputs drift from one another."""

import json
import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

from argus.capabilities import load_mcp_release_descriptor
from argus.runtime_manifest import (
    RuntimeManifestError,
    canonical_json_bytes,
    schema_identity_from_contract,
    sha256_bytes,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]


def _schema_contract_path() -> tuple[str, Path]:
    try:
        from argus.recovery.database import EXPECTED_SCHEMA_HEAD, SCHEMA_CONTRACT_PATHS

        return EXPECTED_SCHEMA_HEAD, Path(SCHEMA_CONTRACT_PATHS[EXPECTED_SCHEMA_HEAD])
    except (ImportError, KeyError):
        candidates = sorted((ROOT / "argus/recovery").glob("argus_schema_*.json"))
        if not candidates:
            raise RuntimeManifestError("checked-in schema contract is unavailable")
        payload = json.loads(candidates[-1].read_text(encoding="utf-8"))
        head = payload.get("schema_head")
        if not isinstance(head, str) or not head:
            raise RuntimeManifestError("checked-in schema contract has no head")
        return head, candidates[-1]


def _verify_schema_contract(
    *,
    schema_contract: Path | None = None,
    schema_head: str | None = None,
) -> dict[str, str]:
    expected_head, default_path = _schema_contract_path()
    path = default_path if schema_contract is None else schema_contract
    expected_head = expected_head if schema_head is None else schema_head
    contract = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict) or contract.get("schema_head") != expected_head:
        raise RuntimeManifestError("checked-in schema contract head is invalid")
    contract_hash = contract.get("contract_sha256")
    if isinstance(contract_hash, str):
        unsigned = dict(contract)
        unsigned.pop("contract_sha256", None)
        if contract_hash != sha256_bytes(canonical_json_bytes(unsigned)):
            raise RuntimeManifestError("checked-in schema contract hash is invalid")
    return schema_identity_from_contract(path, expected_head=expected_head)


def _read_versions() -> tuple[str, str, str, str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    init = (ROOT / "argus/__init__.py").read_text(encoding="utf-8")
    api_main = (ROOT / "argus/api/main.py").read_text(encoding="utf-8")
    package = pyproject["project"]["version"]
    return package, server["version"], server["packages"][0]["version"], init + "\n" + api_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema-contract", type=Path, default=None)
    parser.add_argument("--schema-head", default=None)
    parser.add_argument(
        "--release-descriptor",
        type=Path,
        default=ROOT / "argus/mcp/release_descriptor.json",
    )
    args = parser.parse_args(argv)
    lock_check = subprocess.run(["uv", "lock", "--check"], cwd=ROOT, check=False)
    if lock_check.returncode:
        return lock_check.returncode

    try:
        package, server_version, registry_version, source_versions = _read_versions()
        expected = f'"{package}"'
        if package != server_version or package != registry_version:
            raise RuntimeManifestError("package and server metadata versions are out of sync")
        if f'__version__ = {expected}' not in source_versions:
            raise RuntimeManifestError("argus package version is out of sync")
        if f"version={expected}" not in source_versions:
            raise RuntimeManifestError("API version is out of sync")

        descriptor_digest = sha256_file(args.release_descriptor)
        load_mcp_release_descriptor(args.release_descriptor)
        schema_identity = _verify_schema_contract(
            schema_contract=args.schema_contract,
            schema_head=args.schema_head,
        )
        workflow_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / ".github/workflows").glob("*.y*ml")
        )
        if "twine upload" not in workflow_text:
            raise RuntimeManifestError("release workflow does not publish a package")
        if "twine upload" in workflow_text and "twine upload dist/* || true" in workflow_text:
            raise RuntimeManifestError("package publication is not fatal")
    except (OSError, TypeError, ValueError, RuntimeManifestError) as error:
        print(f"release contract invalid: {error}", file=sys.stderr)
        return 1
    print(
        "release contract valid: "
        f"version={package} schema_head={schema_identity['schema_head']} "
        f"schema_id={schema_identity['schema_id']} "
        f"release_descriptor_sha256={descriptor_digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
