import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "ops/postgres/postgres_recovery.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
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


def test_record_backup_cli_writes_sanitized_evidence(tmp_path):
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

    assert result.returncode == 0, result.stderr
    payload = json.loads(evidence.read_text())
    assert payload["backup"]["databases"] == ["atlas", "argus"]


def test_schema_promotion_gate_cli_exits_nonzero_without_evidence(tmp_path):
    result = _run(
        "promotion-gate",
        "--evidence",
        str(tmp_path / "missing.json"),
        "--schema-change",
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["state"] == "blocked"
