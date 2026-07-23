#!/usr/bin/env python3
"""Build the identity manifest copied into the production image."""

import argparse
import hashlib
import json
import tomllib
from pathlib import Path

from argus.runtime_manifest import (
    EXPECTED_RUNTIME_CAPABILITIES,
    installed_playwright_browser_contract,
    playwright_browser_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--lock-file", type=Path, default=Path("uv.lock"))
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--browser-root", type=Path, default=Path("/ms-playwright"))
    args = parser.parse_args()

    package = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))["project"]
    browser_contract = installed_playwright_browser_contract()
    _, executable_sha256 = playwright_browser_artifact(
        args.browser_root,
        browser_contract["revision"],
    )
    manifest = {
        "source_revision": args.source_revision,
        "package_version": package["version"],
        "schema": {"minimum": 1, "maximum": 1},
        "lock_file": args.lock_file.name,
        "lock_sha256": hashlib.sha256(args.lock_file.read_bytes()).hexdigest(),
        "capabilities": EXPECTED_RUNTIME_CAPABILITIES,
        "browser": {
            **browser_contract,
            "browser_root": str(args.browser_root),
            "executable_sha256": executable_sha256,
        },
    }
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
