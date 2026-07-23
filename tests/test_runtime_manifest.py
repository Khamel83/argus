"""Offline runtime-manifest admission behavior."""

import hashlib
import json

from click.testing import CliRunner


def _manifest(lock_path) -> dict[str, object]:
    lock_path.write_bytes(b"frozen lock contents\n")
    return {
        "source_revision": "f9aa1adaa219c80aef209b7e9b994333b37c3adc",
        "package_version": "1.6.2",
        "schema": {"minimum": 1, "maximum": 1},
        "lock_file": lock_path.name,
        "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        "capabilities": {
            "http_api": True,
            "mcp": True,
            "trafilatura": True,
            "playwright_browser": False,
            "crawl4ai": False,
            "obscura": False,
        },
    }


def test_image_admission_accepts_a_complete_baked_manifest(tmp_path):
    from argus.runtime_manifest import admit_runtime_manifest

    manifest_path = tmp_path / "runtime-manifest.json"
    manifest_path.write_text(
        json.dumps(_manifest(tmp_path / "uv.lock")),
        encoding="utf-8",
    )

    admitted = admit_runtime_manifest(manifest_path, package_version="1.6.2")

    assert admitted["source_revision"] == "f9aa1adaa219c80aef209b7e9b994333b37c3adc"
    assert admitted["capabilities"]["playwright_browser"] is False


def test_image_admission_rejects_missing_required_artifact(tmp_path):
    from argus.runtime_manifest import RuntimeManifestError, admit_runtime_manifest

    missing = tmp_path / "runtime-manifest.json"

    try:
        admit_runtime_manifest(missing, package_version="1.6.2")
    except RuntimeManifestError as error:
        assert "missing" in str(error).lower()
    else:
        raise AssertionError("admission must reject a missing runtime manifest")


def test_image_admission_command_fails_when_package_identity_drifts(tmp_path):
    from argus.cli.main import cli

    manifest_path = tmp_path / "runtime-manifest.json"
    manifest = _manifest(tmp_path / "uv.lock")
    manifest["package_version"] = "0.0.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = CliRunner().invoke(cli, ["image-admission", "--manifest", str(manifest_path)])

    assert result.exit_code != 0
    assert "package version" in result.output.lower()


def test_image_admission_rejects_a_tampered_baked_lock(tmp_path):
    from argus.runtime_manifest import RuntimeManifestError, admit_runtime_manifest

    lock_path = tmp_path / "uv.lock"
    manifest_path = tmp_path / "runtime-manifest.json"
    manifest_path.write_text(json.dumps(_manifest(lock_path)), encoding="utf-8")
    lock_path.write_bytes(b"mutable dependency drift\n")

    try:
        admit_runtime_manifest(manifest_path, package_version="1.6.2")
    except RuntimeManifestError as error:
        assert "lock" in str(error).lower()
    else:
        raise AssertionError("admission must reject a lock artifact that changed after baking")


def test_image_admission_rejects_capability_claims_that_drift(tmp_path):
    from argus.runtime_manifest import RuntimeManifestError, admit_runtime_manifest

    lock_path = tmp_path / "uv.lock"
    manifest_path = tmp_path / "runtime-manifest.json"
    manifest = _manifest(lock_path)
    manifest["capabilities"]["mcp"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        admit_runtime_manifest(manifest_path, package_version="1.6.2")
    except RuntimeManifestError as error:
        assert "capabilit" in str(error).lower()
    else:
        raise AssertionError("admission must reject false runtime capability claims")
