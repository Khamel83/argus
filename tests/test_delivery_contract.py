"""Release inputs must remain reproducible and internally consistent."""

import re
import subprocess
import sys
from pathlib import Path


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


def test_every_ci_uv_install_is_version_pinned():
    ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    install_commands = re.findall(
        r"\bpython3? -m pip install uv(?:==[^\s]+)?",
        ci,
    )

    assert len(install_commands) == 3
    assert all(command in {
        f"python -m pip install uv=={UV_VERSION}",
        f"python3 -m pip install uv=={UV_VERSION}",
    } for command in install_commands)
    assert "pip install --upgrade pip" not in ci


def test_pull_request_ci_builds_the_production_image_without_pushing():
    ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "image-build:" in ci
    assert "docker/build-push-action@v5" in ci
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
    assert dockerfile.count(f"FROM {PYTHON_IMAGE}") == 2
    assert "apt-get" not in dockerfile
