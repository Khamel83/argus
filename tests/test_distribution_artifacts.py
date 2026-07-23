from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_wheel_configuration_includes_alembic_runtime_artifacts():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    data_files = config["tool"]["setuptools"]["data-files"]

    assert "alembic.ini" in data_files["."]
    assert {
        "migrations/env.py",
        "migrations/script.py.mako",
    } <= set(data_files["migrations"])
    assert (
        "migrations/versions/0001_search_ledger.py" in data_files["migrations/versions"]
    )
    assert (
        "migrations/versions/0002_acceptance_fingerprint.py"
        in data_files["migrations/versions"]
    )
    assert (
        "migrations/versions/0003_request_routing_fields.py"
        in data_files["migrations/versions"]
    )
    assert (
        "migrations/versions/0004_operation_ledger.py"
        in data_files["migrations/versions"]
    )
    assert (
        "migrations/versions/0005_provider_spend.py"
        in data_files["migrations/versions"]
    )
    assert (
        "migrations/versions/0006_maya_outbox.py" in data_files["migrations/versions"]
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
