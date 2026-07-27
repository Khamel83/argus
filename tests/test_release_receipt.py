"""Immutable release receipt contract."""

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/write_release_receipt.py"
DIGEST = "sha256:" + ("a" * 64)
REVISION = "b" * 40


def _run(
    tmp_path: Path,
    *,
    image: str = "ghcr.io/khamel83/argus",
    digest: str = DIGEST,
    revision: str = REVISION,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    output = tmp_path / "release.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--image",
            image,
            "--digest",
            digest,
            "--source-revision",
            revision,
            "--repository",
            "Khamel83/argus",
            "--workflow",
            "Build and Promote Immutable Image",
            "--run-id",
            "1234",
            "--run-attempt",
            "2",
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return result, output


def test_release_receipt_is_digest_addressed_and_stable(tmp_path: Path):
    result, output = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload == {
        "build": {
            "repository": "Khamel83/argus",
            "run_attempt": "2",
            "run_id": "1234",
            "workflow": "Build and Promote Immutable Image",
        },
        "digest": DIGEST,
        "image": "ghcr.io/khamel83/argus",
        "image_ref": f"ghcr.io/khamel83/argus@{DIGEST}",
        "schema_version": 1,
        "source_revision": REVISION,
    }
    assert output.read_bytes().endswith(b"\n")


def test_release_receipt_rejects_mutable_image_input(tmp_path: Path):
    result, output = _run(tmp_path, image="ghcr.io/khamel83/argus:latest")

    assert result.returncode != 0
    assert "untagged ghcr.io owner/repository name" in result.stderr
    assert not output.exists()


def test_release_receipt_rejects_non_sha256_digest(tmp_path: Path):
    result, output = _run(tmp_path, digest="sha256:not-a-digest")

    assert result.returncode != 0
    assert "sha256 followed by 64 lowercase hex characters" in result.stderr
    assert not output.exists()


def test_release_receipt_rejects_short_source_revision(tmp_path: Path):
    result, output = _run(tmp_path, revision="abc123")

    assert result.returncode != 0
    assert "full lowercase Git commit" in result.stderr
    assert not output.exists()
