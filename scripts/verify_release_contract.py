#!/usr/bin/env python3
"""Fail fast when reproducible release inputs drift from one another."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read_versions() -> tuple[str, str, str, str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    init = (ROOT / "argus/__init__.py").read_text(encoding="utf-8")
    api_main = (ROOT / "argus/api/main.py").read_text(encoding="utf-8")
    package = pyproject["project"]["version"]
    return package, server["version"], server["packages"][0]["version"], init + "\n" + api_main


def main() -> int:
    lock_check = subprocess.run(["uv", "lock", "--check"], cwd=ROOT, check=False)
    if lock_check.returncode:
        return lock_check.returncode

    package, server_version, registry_version, source_versions = _read_versions()
    expected = f'"{package}"'
    if package != server_version or package != registry_version:
        print("package and server metadata versions are out of sync", file=sys.stderr)
        return 1
    if f'__version__ = {expected}' not in source_versions:
        print("argus package version is out of sync", file=sys.stderr)
        return 1
    if f"version={expected}" not in source_versions:
        print("API version is out of sync", file=sys.stderr)
        return 1
    print(f"release contract valid: version={package}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
