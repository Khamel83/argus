"""Release inputs must remain reproducible and internally consistent."""

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = "0.11.26"
UV_IMAGE = (
    "ghcr.io/astral-sh/uv:0.11.26@"
    "sha256:3d868e555f8f1dbc324afa005066cd11e1053fc4743b9808ca8025283e65efa5"
)
PYTHON_IMAGE = (
    "python:3.12.13-slim-bookworm@"
    "sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b"
)
PLAYWRIGHT_IMAGE = (
    "mcr.microsoft.com/playwright/python:v1.58.0-noble@"
    "sha256:678457c4c323b981d8b4befc57b95366bb1bb6aa30057b1269f6b171e8d9975a"
)


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
    assert "mise run production-config" in ci
    tasks = tomllib.loads((REPO_ROOT / "mise.toml").read_text())["tasks"]
    assert "tests/test_production_config.py" in tasks["production-config"]["run"]


def _mise_fixture(tmp_path):
    mise = shutil.which("mise")
    if mise is None or os.name != "posix":
        pytest.skip("the mise runtime fixture needs mise and POSIX symlinks")
    (tmp_path / "mise.toml").write_bytes((REPO_ROOT / "mise.toml").read_bytes())
    home = tmp_path / "different-home"
    home.mkdir()
    runtime = tmp_path / "selected-runtime"
    (runtime / "bin").mkdir(parents=True)
    # A tool root without an installed uv keeps fake-command tests hermetic.
    (runtime / "bin/python").symlink_to(Path(sys.base_prefix) / "bin/python")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("MISE_", "UV_"))}
    env.update({
        "HOME": str(home),
        "MISE_CONFIG_DIR": str(tmp_path / "config"),
        "MISE_DATA_DIR": str(tmp_path / "data"),
        "MISE_CACHE_DIR": str(tmp_path / "cache"),
        "MISE_STATE_DIR": str(tmp_path / "state"),
        "MISE_TRUSTED_CONFIG_PATHS": str(tmp_path),
        "MISE_PYTHON_VERSION": f"path:{runtime}",
        "MISE_AUTO_INSTALL": "false",
    })
    return mise, env


def test_mise_binds_the_actual_interpreter_independently_of_home(tmp_path):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for the runtime binding check")
    mise, env = _mise_fixture(tmp_path)
    result = subprocess.run(
        [mise, "exec", "--", "python", "-c",
         "import json,os,sys; print(json.dumps({'python':sys.executable,"
         "'uv_python':os.environ['UV_PYTHON'],"
         "'downloads':os.environ['UV_PYTHON_DOWNLOADS']}))"],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    selected = json.loads(result.stdout)
    found = subprocess.run(
        [uv, "python", "find", selected["uv_python"], "--offline", "--no-project"],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30,
    )
    assert found.returncode == 0, found.stdout + found.stderr
    assert Path(found.stdout.strip()).resolve() == Path(selected["python"]).resolve()
    assert selected["downloads"] == "never"


def test_mise_test_task_preserves_the_existing_pytest_invocation(tmp_path):
    mise, env = _mise_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text("#!/bin/sh\nprintf '%s\\n' \"$UV_PYTHON_DOWNLOADS\" \"$@\"\n")
    uv.chmod(0o755)
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    result = subprocess.run(
        [mise, "run", "test"], cwd=tmp_path, env=env,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "never", "run", "--frozen", "pytest", "tests/", "-v", "--tb=short",
    ]


def test_every_ci_uv_install_is_version_pinned():
    ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    install_commands = re.findall(
        r"\bpython3? -m pip install uv(?:==[^\s]+)?",
        ci,
    )

    assert len(install_commands) == ci.count("- name: Install uv")
    assert all(command in {
        f"python -m pip install uv=={UV_VERSION}",
        f"python3 -m pip install uv=={UV_VERSION}",
    } for command in install_commands)
    assert "pip install --upgrade pip" not in ci


def test_pull_request_ci_builds_the_production_image_without_pushing():
    ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "image-build:" in ci
    assert (
        "docker/build-push-action@"
        "ca052bb54ab0790a636c9b5f226502c73d547a25 # v5"
    ) in ci
    assert "push: false" in ci
    assert "VCS_REF=${{ github.sha }}" in ci


def test_compose_build_supplies_an_explicit_source_revision():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'VCS_REF: "${ARGUS_IMAGE_VCS_REF:-local-compose-build}"' in compose
    assert 'IMAGE_ADMISSION_POLICY: "${ARGUS_IMAGE_ADMISSION_POLICY:-development}"' in compose
    assert "# Production builds must set ARGUS_IMAGE_VCS_REF" in compose
    assert "ARG IMAGE_ADMISSION_POLICY=production" in dockerfile
    assert "--allow-development-revision" in dockerfile


def test_compose_persistence_does_not_mask_immutable_runtime_artifacts():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ARGUS_DATA_ROOT=/var/lib/argus" in compose
    assert (
        "ARGUS_DB_URL=${ARGUS_DB_URL:-sqlite:////var/lib/argus/argus.db}"
        in compose
    )
    assert "ARGUS_BUDGET_DB_PATH=/var/lib/argus/argus_budgets.db" in compose
    assert "argus-data:/var/lib/argus" in compose
    assert "argus-data:/app" not in compose
    assert "mkdir -p /var/lib/argus" in dockerfile
    assert "chown -R argus:argus /app /var/lib/argus" in dockerfile


def test_production_dockerfile_uses_the_frozen_lock_and_bakes_runtime_manifest():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "uv sync --frozen" in dockerfile
    assert "uv.lock" in dockerfile
    assert "runtime-manifest.json" in dockerfile
    assert "image-admission" in dockerfile
    assert "ARG VCS_REF\n" in dockerfile
    assert "COPY pyproject.toml uv.lock README.md ./" in dockerfile
    assert "RUN /app/.venv/bin/python scripts/build_runtime_manifest.py" in dockerfile
    assert f"FROM {UV_IMAGE} AS uv" in dockerfile
    assert dockerfile.count(f"FROM {PYTHON_IMAGE}") == 0
    assert dockerfile.count(f"FROM {PLAYWRIGHT_IMAGE}") == 2
    assert "apt-get" not in dockerfile
