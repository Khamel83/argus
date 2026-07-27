#!/usr/bin/env python3
"""Validate and write the immutable container build receipt."""

import argparse
import json
import re
from pathlib import Path


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_IMAGE = re.compile(
    r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+\Z",
    re.IGNORECASE,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if not _IMAGE.fullmatch(args.image):
        parser.error("--image must be an untagged ghcr.io owner/repository name")
    if not _DIGEST.fullmatch(args.digest):
        parser.error(
            "--digest must be sha256 followed by 64 lowercase hex characters"
        )
    if not _REVISION.fullmatch(args.source_revision):
        parser.error("--source-revision must be a full lowercase Git commit")

    payload = {
        "schema_version": 1,
        "image": args.image,
        "image_ref": f"{args.image}@{args.digest}",
        "digest": args.digest,
        "source_revision": args.source_revision,
        "build": {
            "repository": args.repository,
            "workflow": args.workflow,
            "run_id": args.run_id,
            "run_attempt": args.run_attempt,
        },
    }
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
