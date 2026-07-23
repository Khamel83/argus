"""Offline runtime-manifest admission behavior."""

import hashlib
import json
import os

from click.testing import CliRunner


def _browser_contract(browser_root) -> dict[str, str]:
    from argus.runtime_manifest import installed_playwright_browser_contract

    contract = installed_playwright_browser_contract()
    browser_dir = browser_root / f"chromium_headless_shell-{contract['revision']}"
    executable = browser_dir / "chrome-headless-shell-linux64" / "chrome-headless-shell"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"test browser")
    executable.chmod(0o755)
    return {
        **contract,
        "browser_root": os.fspath(browser_root),
    }


def _manifest(lock_path, browser_root=None) -> dict[str, object]:
    lock_path.write_bytes(b"frozen lock contents\n")
    if browser_root is None:
        browser_root = lock_path.parent / "ms-playwright"
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
            "playwright_browser": True,
            "crawl4ai": False,
            "obscura": False,
        },
        "browser": _browser_contract(browser_root),
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
    assert admitted["capabilities"]["playwright_browser"] is True
    assert admitted["browser"]["revision"]


def test_image_admission_rejects_missing_matched_headless_shell(tmp_path):
    from argus.runtime_manifest import RuntimeManifestError, admit_runtime_manifest

    lock_path = tmp_path / "uv.lock"
    manifest = _manifest(lock_path)
    browser_root = tmp_path / "ms-playwright"
    for executable_name in ("chrome-headless-shell", "headless_shell"):
        for executable in browser_root.rglob(executable_name):
            executable.unlink()
    manifest_path = tmp_path / "runtime-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        admit_runtime_manifest(manifest_path, package_version="1.6.2")
    except RuntimeManifestError as error:
        assert "headless shell" in str(error).lower()
    else:
        raise AssertionError("production admission must reject a missing browser")


def test_image_admission_rejects_playwright_browser_revision_drift(tmp_path):
    from argus.runtime_manifest import RuntimeManifestError, admit_runtime_manifest

    lock_path = tmp_path / "uv.lock"
    manifest = _manifest(lock_path)
    manifest["browser"]["revision"] = "wrong"
    manifest_path = tmp_path / "runtime-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        admit_runtime_manifest(manifest_path, package_version="1.6.2")
    except RuntimeManifestError as error:
        assert "browser contract" in str(error).lower()
    else:
        raise AssertionError("production admission must reject browser revision drift")


def test_production_image_admission_rejects_a_local_revision_marker(tmp_path):
    from argus.runtime_manifest import RuntimeManifestError, admit_runtime_manifest

    manifest_path = tmp_path / "runtime-manifest.json"
    manifest = _manifest(tmp_path / "uv.lock")
    manifest["source_revision"] = "local-compose-build"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        admit_runtime_manifest(manifest_path, package_version="1.6.2")
    except RuntimeManifestError as error:
        assert "full commit" in str(error).lower()
    else:
        raise AssertionError("production admission must reject a development revision")


def test_development_image_validation_is_explicit_and_not_production_admission(tmp_path):
    from argus.cli.main import cli

    manifest_path = tmp_path / "runtime-manifest.json"
    manifest = _manifest(tmp_path / "uv.lock")
    manifest["source_revision"] = "local-compose-build"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "image-admission",
            "--manifest",
            str(manifest_path),
            "--allow-development-revision",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "development-validated" in result.output
    assert "production-admitted" not in result.output


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
