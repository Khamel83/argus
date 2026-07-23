"""Offline runtime-manifest admission behavior."""

import json

from click.testing import CliRunner


def _manifest() -> dict[str, object]:
    return {
        "source_revision": "f9aa1adaa219c80aef209b7e9b994333b37c3adc",
        "package_version": "1.6.2",
        "schema": {"minimum": 1, "maximum": 1},
        "lock_sha256": "a" * 64,
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
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")

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
    manifest = _manifest()
    manifest["package_version"] = "0.0.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = CliRunner().invoke(cli, ["image-admission", "--manifest", str(manifest_path)])

    assert result.exit_code != 0
    assert "package version" in result.output.lower()
