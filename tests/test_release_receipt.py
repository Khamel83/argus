"""Immutable release receipt contract."""

import json
import hashlib
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
    descriptor = tmp_path / "release-descriptor.json"
    descriptor.write_text('{"descriptor_version": 1}\n', encoding="utf-8")
    schema = tmp_path / "schema-contract.json"
    schema.write_text(
        json.dumps(
            {
                "schema_head": "0011_extraction_spend_scope",
                "migration_chain_sha256": "a" * 64,
                "canonical_postgresql_schema_sha256": "b" * 64,
                "schema_contract_format": "argus-schema-contract-v1",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "source_revision": revision,
                "release_descriptor_file": descriptor.name,
                "release_descriptor_digest": hashlib.sha256(
                    descriptor.read_bytes()
                ).hexdigest(),
                "schema_contract_file": schema.name,
                "schema_contract_sha256": hashlib.sha256(
                    schema.read_bytes()
                ).hexdigest(),
                "schema_identity": {
                    "schema_head": "0011_extraction_spend_scope",
                    "migration_chain_sha256": "a" * 64,
                    "canonical_postgresql_schema_sha256": "b" * 64,
                    "schema_contract_format": "argus-schema-contract-v1",
                },
                "schema_head": "0011_extraction_spend_scope",
                "migration_chain_sha256": "a" * 64,
                "canonical_postgresql_schema_sha256": "b" * 64,
                "schema_contract_format": "argus-schema-contract-v1",
            }
        ),
        encoding="utf-8",
    )
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
            "--runtime-manifest",
            str(manifest),
            "--release-descriptor",
            str(descriptor),
            "--schema-contract",
            str(schema),
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
    assert payload["schema_version"] == 2
    assert payload["image_ref"] == f"ghcr.io/khamel83/argus@{DIGEST}"
    assert payload["release_identity"]["source_revision"] == REVISION
    assert payload["release_identity"]["image_digest"] == DIGEST
    assert payload["release_identity"]["release_descriptor_digest"] == hashlib.sha256(
        (tmp_path / "release-descriptor.json").read_bytes()
    ).hexdigest()
    assert payload["release_identity"]["runtime_manifest_digest"] == hashlib.sha256(
        (tmp_path / "runtime-manifest.json").read_bytes()
    ).hexdigest()
    assert payload["schema_identity"]["schema_head"] == "0011_extraction_spend_scope"
    assert len(payload["receipt_identity"]) == 64
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
