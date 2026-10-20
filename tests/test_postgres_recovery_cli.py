import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "ops/postgres/postgres_recovery.py"
BACKUP_SCRIPT = Path(__file__).parents[1] / "ops/postgres/backup_shared_postgres.sh"
RESTORE_SCRIPT = Path(__file__).parents[1] / "ops/postgres/verify_restore.sh"


def _run(*args, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def _run_restore(tmp_path, *, isolated=False, wrapper="", env_overrides=None):
    backup = tmp_path / "20261019T120000Z"
    backup.mkdir(parents=True)
    for name, content in {
        "argus.dump": b"argus archive",
        "atlas.dump": b"atlas archive",
        "globals.sql": b"CREATE ROLE argus;\n",
    }.items():
        (backup / name).write_bytes(content)
    sums = "".join(
        f"{hashlib.sha256((backup / name).read_bytes()).hexdigest()}  {name}\n"
        for name in ("argus.dump", "atlas.dump", "globals.sql")
    )
    (backup / "SHA256SUMS").write_text(sums, encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace = tmp_path / "trace"
    (fake_bin / "python3").write_text(
        '#!/bin/sh\nprintf "python %s\\n" "$*" >> "$TRACE"\n',
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text(
        '#!/bin/sh\nprintf "docker %s\\n" "$*" >> "$TRACE"\n'
        'case "$*" in\n'
        '  *"{{index .Config.Labels"*) echo "${TEST_DOCKER_LABEL:-$ARGUS_ISOLATED_RESTORE_RUN_LABEL}"; exit 0 ;;\n'
        '  *"{{.Name}}"*) echo "${TEST_DOCKER_NAME:-/argus-restore-fixture}"; exit 0 ;;\n'
        '  "port "*) echo "${TEST_DOCKER_PORT:-127.0.0.1:$PGPORT}"; exit 0 ;;\n'
        'esac\n'
        'case " $* " in *" pg_restore "*) cat >/dev/null ;; esac\n',
        encoding="utf-8",
    )
    (fake_bin / "python3").chmod(0o755)
    (fake_bin / "docker").chmod(0o755)
    live_data = tmp_path / "live-data"
    live_data.mkdir()
    evidence = tmp_path / "evidence.json"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TRACE": str(trace),
        "ARGUS_BACKUP_SET": str(backup),
        "ARGUS_BACKUP_ROOT": str(tmp_path),
        "POSTGRES_LIVE_DATA_DIR": str(live_data),
        "ARGUS_RECOVERY_EVIDENCE": str(evidence),
        "SCRATCH_DATABASE": "argus_restore_isolated_test",
        "ATLAS_SCRATCH_DATABASE": "atlas_restore_isolated_test",
        "ARGUS_PG_CONTAINER": "a" * 64,
        "PGHOST": "127.0.0.1",
        "PGPORT": "54321",
        "ARGUS_ISOLATED_RESTORE_MIGRATION": "true" if isolated else "false",
        "ARGUS_ISOLATED_RESTORE_WRAPPER": wrapper,
        "ARGUS_ISOLATED_RESTORE_RUN_LABEL": "restore-20261019T120000Z-1234",
    }
    env.update(env_overrides or {})
    result = subprocess.run(
        ["/bin/sh", str(RESTORE_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result, trace.read_text(encoding="utf-8") if trace.exists() else ""


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


def test_retention_cli_has_no_apply_or_prune_mode(tmp_path):
    forbidden_apply = _run(
        "retention-plan",
        "--root",
        str(tmp_path / "backups"),
        "--live-data",
        str(tmp_path / "live"),
        "--apply",
    )
    removed_prune = _run(
        "prune",
        "--root",
        str(tmp_path / "backups"),
        "--live-data",
        str(tmp_path / "live"),
    )

    assert forbidden_apply.returncode != 0
    assert "unrecognized arguments: --apply" in forbidden_apply.stderr
    assert removed_prune.returncode != 0
    assert "invalid choice" in removed_prune.stderr


def test_shared_backup_script_supports_container_archive_streaming():
    source = BACKUP_SCRIPT.read_text(encoding="utf-8")

    assert "ARGUS_PG_CONTAINER" in source
    assert "docker exec -i" in source
    assert "pg_restore --list < \"$1\"" in source


def test_shared_backup_failure_never_publishes_staging_set(tmp_path):
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    (backup_root / ".argus-shared-postgres-backup-root.json").write_text("{}\n")
    live = tmp_path / "live"
    live.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        '#!/bin/sh\ncase " $* " in *" capture-database "*) exit 9;; esac\nexit 0\n'
    )
    fake_python.chmod(0o755)
    fake_docker = fake_bin / "docker"
    fake_docker.write_text("#!/bin/sh\nexit 0\n")
    fake_docker.chmod(0o755)
    evidence = tmp_path / "evidence.json"

    result = subprocess.run(
        ["/bin/sh", str(BACKUP_SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "ARGUS_BACKUP_ROOT": str(backup_root),
            "POSTGRES_LIVE_DATA_DIR": str(live),
            "ARGUS_RECOVERY_EVIDENCE": str(evidence),
            "ARGUS_PG_CONTAINER": "atlas-postgres",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert not evidence.exists()
    assert list(backup_root.iterdir()) == [
        backup_root / ".argus-shared-postgres-backup-root.json"
    ]


def test_restore_script_supports_container_archive_streaming():
    source = RESTORE_SCRIPT.read_text(encoding="utf-8")

    assert "ARGUS_PG_CONTAINER" in source
    assert "docker exec -i" in source
    assert "createdb" in source
    assert "dropdb" in source


def test_restore_script_container_default_still_skips_migration(tmp_path):
    result, trace = _run_restore(tmp_path)

    assert result.returncode == 0, result.stderr
    record = next(line for line in trace.splitlines() if " record-restore " in line)
    assert "--skip-migration" in record
    assert "ARGUS_ISOLATED_RESTORE_MIGRATION_VERIFIED=1" not in result.stdout


def test_restore_script_isolated_wrapper_runs_migration(tmp_path):
    result, trace = _run_restore(tmp_path, isolated=True, wrapper="infra-v1")

    assert result.returncode == 0, result.stderr
    record = next(line for line in trace.splitlines() if " record-restore " in line)
    assert "--skip-migration" not in record
    assert result.stdout.strip().endswith("ARGUS_ISOLATED_RESTORE_MIGRATION_VERIFIED=1")


def test_restore_script_rejects_migration_outside_isolated_wrapper(tmp_path):
    result, trace = _run_restore(tmp_path, isolated=True)

    assert result.returncode == 2
    assert "migration requires the isolated Infra restore wrapper" in result.stderr
    assert trace == ""


def test_restore_script_rejects_live_or_short_container_before_createdb(tmp_path):
    result, trace = _run_restore(
        tmp_path,
        isolated=True,
        wrapper="infra-v1",
        env_overrides={"ARGUS_PG_CONTAINER": "atlas-postgres"},
    )

    assert result.returncode == 2
    assert "full container ID" in result.stderr
    assert " createdb " not in trace


def test_restore_script_rejects_mismatched_label_name_and_port(tmp_path):
    cases = (
        ({"TEST_DOCKER_LABEL": "restore-20261019T120000Z-9999"}, "ownership"),
        ({"TEST_DOCKER_NAME": "/atlas-postgres"}, "non-restore"),
        ({"TEST_DOCKER_PORT": "0.0.0.0:54321"}, "loopback endpoint"),
    )
    for index, (overrides, message) in enumerate(cases):
        result, trace = _run_restore(
            tmp_path / str(index),
            isolated=True,
            wrapper="infra-v1",
            env_overrides=overrides,
        )
        assert result.returncode == 2
        assert message in result.stderr
        assert " createdb " not in trace


def test_record_restore_cli_documents_container_migration_skip():
    result = _run("record-restore", "--help")

    assert result.returncode == 0
    assert "--skip-migration" in result.stdout
    assert "container" in result.stdout.lower()


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
