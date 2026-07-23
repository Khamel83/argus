import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "ops/postgres/postgres_recovery.py"


def _run(*args, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def test_validate_scratch_cli_fails_closed():
    valid = _run(
        "validate-scratch",
        "--database",
        "argus_restore_issue40_cli",
    )
    unsafe = _run("validate-scratch", "--database", "argus")

    assert valid.returncode == 0
    assert valid.stdout.strip() == "argus_restore_issue40_cli"
    assert unsafe.returncode != 0
    assert "scratch database" in unsafe.stderr


def test_record_backup_cli_cannot_mint_evidence_from_asserted_hash(tmp_path):
    evidence = tmp_path / "evidence.json"

    result = _run(
        "record-backup",
        "--evidence",
        str(evidence),
        "--completed-at",
        "20260723T080000Z",
        "--manifest-sha256",
        "a" * 64,
    )

    assert result.returncode != 0
    assert not evidence.exists()


def test_record_restore_cli_cannot_mint_evidence_from_asserted_schema(tmp_path):
    evidence = tmp_path / "evidence.json"

    result = _run(
        "record-restore",
        "--evidence",
        str(evidence),
        "--schema-head",
        "0005_provider_spend",
    )

    assert result.returncode != 0
    assert not evidence.exists()


def test_schema_promotion_gate_cli_exits_nonzero_without_evidence(tmp_path):
    result = _run(
        "promotion-gate",
        "--evidence",
        str(tmp_path / "missing.json"),
        "--schema-change",
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["state"] == "blocked"


def test_import_rejects_credentialed_url_without_echoing_secret():
    result = _run(
        "import",
        env={
            "ARGUS_DB_URL": "postgresql://operator:topsecret@db/argus",
            "LEGACY_SEARCH_DB_URL": "sqlite:////safe/search.db",
            "LEGACY_SESSION_DB_URL": "sqlite:////safe/sessions.db",
        },
    )

    assert result.returncode == 2
    assert "credential-free" in result.stderr
    assert "topsecret" not in result.stderr
    assert "topsecret" not in result.stdout


def test_operator_cli_sanitizes_unexpected_database_errors():
    result = _run(
        "import",
        env={
            "ARGUS_DB_URL": "postgresql+psycopg2:///argus",
            "LEGACY_SEARCH_DB_URL": "sqlite:////missing/operator/search.db",
            "LEGACY_SESSION_DB_URL": "sqlite:////missing/operator/sessions.db",
        },
    )

    assert result.returncode == 2
    assert "operation failed" in result.stderr
    assert "Traceback" not in result.stderr
    assert "/missing/operator" not in result.stderr
