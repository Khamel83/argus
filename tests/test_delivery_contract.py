"""Release inputs must remain reproducible and internally consistent."""

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_release_contract_validates_frozen_lock_and_metadata():
    result = subprocess.run(
        [sys.executable, "scripts/verify_release_contract.py"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_ci_runs_the_suite_with_hermetic_runtime_configuration():
    ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "ARGUS_AUTOLOAD_DOTENV: \"false\"" in ci
    assert "ARGUS_DISABLE_SECRET_RESOLUTION: \"true\"" in ci
    assert "ARGUS_EGRESS_TYPE: \"unknown\"" in ci
    assert "ARGUS_RESIDENTIAL_POLICY: \"off\"" in ci
    assert "tests/test_production_config.py" in ci


def test_production_dockerfile_uses_the_frozen_lock_and_bakes_runtime_manifest():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "uv sync --frozen" in dockerfile
    assert "uv.lock" in dockerfile
    assert "runtime-manifest.json" in dockerfile
    assert "image-admission" in dockerfile
    assert "ARG VCS_REF\n" in dockerfile
