from pathlib import Path
import subprocess
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def test_wheel_configuration_includes_alembic_runtime_artifacts():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    data_files = config["tool"]["setuptools"]["data-files"]

    assert "alembic.ini" in data_files["."]
    assert {
        "migrations/env.py",
        "migrations/script.py.mako",
    } <= set(data_files["migrations"])
    assert data_files["migrations/versions"] == ["migrations/versions/*.py"]


def test_built_wheel_installs_complete_migration_chain(tmp_path):
    from argus.recovery.database import EXPECTED_SCHEMA_HEAD

    wheel_dir = tmp_path / "wheel"
    install_dir = tmp_path / "installed"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert any(name.endswith(
        ".data/data/migrations/versions/0007_extraction_outcomes.py"
    ) for name in names)
    assert any(name.endswith(
        ".data/data/migrations/versions/0008_provider_readiness.py"
    ) for name in names)
    assert any(name.endswith(
        ".data/data/migrations/versions/0009_retrieval_evidence.py"
    ) for name in names)
    assert any(name.endswith(
        f".data/data/migrations/versions/{EXPECTED_SCHEMA_HEAD}.py"
    ) for name in names)

    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(install_dir),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(install_dir / "alembic.ini"))
    config.set_main_option("script_location", str(install_dir / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == (
        EXPECTED_SCHEMA_HEAD
    )


def test_production_image_copies_alembic_runtime_artifacts():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert dockerfile.count("COPY alembic.ini ./") == 2
    assert dockerfile.count("COPY migrations/ ./migrations/") == 2


def test_postgresql_ci_runs_real_api_commit_failure_contract():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert (
        "tests/test_api.py::TestSearchEndpoint::"
        "test_postgresql_constraint_failure_returns_503_and_rolls_back_ledger"
        in workflow
    )
    assert (
        "tests/test_operation_ledger.py::"
        "test_postgresql_extraction_and_session_contract" in workflow
    )
    assert "tests/test_provider_spend.py" in workflow


def test_publish_regenerates_release_specific_provider_attestations():
    workflow = (ROOT / ".github/workflows/publish.yml").read_text()

    assert "Generate release-specific provider attestations" in workflow
    assert "--release-revision \"argus-${PACKAGE_VERSION}\"" in workflow
    assert "generate_provider_fixture_attestations.py" in workflow
