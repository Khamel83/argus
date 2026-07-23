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
    assert "migrations/versions/0001_search_ledger.py" in data_files[
        "migrations/versions"
    ]
    assert "migrations/versions/0002_acceptance_fingerprint.py" in data_files[
        "migrations/versions"
    ]


def test_production_image_copies_alembic_runtime_artifacts():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert dockerfile.count("COPY alembic.ini ./") == 2
    assert dockerfile.count("COPY migrations/ ./migrations/") == 2
