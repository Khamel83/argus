from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


def test_scratch_database_requires_explicit_disposable_name():
    from argus.recovery.operator import validate_scratch_database

    assert (
        validate_scratch_database("argus_restore_issue40_20260723")
        == "argus_restore_issue40_20260723"
    )
    assert (
        validate_scratch_database(
            "atlas_restore_issue40_20260723",
            tenant="atlas",
        )
        == "atlas_restore_issue40_20260723"
    )
    for unsafe in (
        "argus",
        "atlas",
        "postgres",
        "template0",
        "argus_restore",
        "argus_restore_../../argus",
        "ARGUS_RESTORE_prod",
    ):
        with pytest.raises(ValueError):
            validate_scratch_database(unsafe)


def test_database_inputs_reject_uris_userinfo_passwords_and_query_secrets():
    from argus.recovery.operator import (
        validate_credential_free_database_url,
        validate_database_name,
    )

    assert validate_database_name("argus", allowed={"argus"}) == "argus"
    assert (
        validate_credential_free_database_url(
            "postgresql+psycopg2:///argus",
            allowed_database="argus",
        )
        == "postgresql+psycopg2:///argus"
    )
    for unsafe in (
        "postgresql://user:secret@host/argus",
        "postgresql://host/argus?password=secret",
        "postgresql://host/argus?sslkey=/secret/key",
    ):
        with pytest.raises(ValueError, match="credential-free"):
            validate_credential_free_database_url(
                unsafe,
                allowed_database="argus",
            )
    for unsafe_name in ("postgresql://host/argus", "argus?password=x", "user@argus"):
        with pytest.raises(ValueError, match="plain database name"):
            validate_database_name(unsafe_name)


def test_backup_root_must_be_canonically_outside_live_data(tmp_path):
    from argus.recovery.operator import (
        initialize_backup_root,
        validate_backup_root,
    )

    live = tmp_path / "postgres-data"
    live.mkdir()
    outside = tmp_path / "backups"
    outside.mkdir()

    initialize_backup_root(outside, live_data=live)
    assert validate_backup_root(outside, live_data=live) == outside.resolve()
    with pytest.raises(ValueError, match="outside"):
        validate_backup_root(live, live_data=live)
    nested = live / "logical-backups"
    nested.mkdir()
    with pytest.raises(ValueError, match="outside"):
        initialize_backup_root(nested, live_data=live)
    ancestor = tmp_path
    with pytest.raises(ValueError, match="outside"):
        initialize_backup_root(ancestor, live_data=live)
    missing = tmp_path / "missing-live"
    fresh = tmp_path / "fresh-backups"
    fresh.mkdir()
    with pytest.raises(ValueError, match="live data"):
        initialize_backup_root(fresh, live_data=missing)


def test_backup_root_requires_owned_marker_and_rejects_symlink(tmp_path):
    from argus.recovery.operator import initialize_backup_root, validate_backup_root

    live = tmp_path / "live"
    live.mkdir()
    root = tmp_path / "backups"
    root.mkdir()
    with pytest.raises(ValueError, match="marker"):
        validate_backup_root(root, live_data=live)

    initialize_backup_root(root, live_data=live)
    alias = tmp_path / "backup-alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        validate_backup_root(alias, live_data=live)


def test_alias_validation_requires_same_resolved_endpoint():
    from argus.recovery.operator import validate_compatibility_alias

    addresses = {
        "homelab-postgres": {"10.20.0.4"},
        "atlas-postgres": {"10.20.0.4"},
        "other": {"10.20.0.5"},
    }

    def resolve(host):
        return addresses[host]

    assert validate_compatibility_alias(
        "homelab-postgres", "atlas-postgres", resolver=resolve
    ) == {
        "primary": "homelab-postgres",
        "compatibility": "atlas-postgres",
        "valid": True,
    }
    with pytest.raises(ValueError, match="same endpoint"):
        validate_compatibility_alias(
            "homelab-postgres", "other", resolver=resolve
        )


def test_retention_keeps_at_least_7_daily_5_weekly_and_12_monthly_sets():
    from argus.recovery.operator import retained_snapshot_names

    snapshots = [
        f"2025{month:02d}01T010000Z" for month in range(1, 13)
    ] + [
        f"202607{day:02d}T020000Z" for day in range(1, 24)
    ]

    kept = retained_snapshot_names(
        snapshots,
        now=datetime(2026, 7, 23, 8, tzinfo=timezone.utc),
    )

    kept_days = {name[:8] for name in kept if name.startswith("202607")}
    kept_months = {name[:6] for name in kept}
    assert len(kept_days) >= 7
    assert len(kept_months) >= 12
    assert "20260723T020000Z" in kept


def test_provisioning_sql_declares_isolated_roles_without_credentials():
    sql = (ROOT / "ops/postgres/provision_shared_postgres.sql").read_text()

    for tenant in ("atlas", "argus"):
        for role in ("owner", "migration", "runtime", "readonly", "backup"):
            assert f"{tenant}_{role}" in sql
    assert "ALTER DEFAULT PRIVILEGES" in sql
    assert sql.count("ON ALL TABLES IN SCHEMA public") >= 4
    assert sql.count("ON ALL SEQUENCES IN SCHEMA public") >= 4
    assert "REVOKE ALL ON DATABASE" in sql
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC" in sql
    assert "PASSWORD" not in sql.upper()


def test_provisioning_sql_normalizes_poisoned_role_attributes_and_memberships():
    sql = (ROOT / "ops/postgres/provision_shared_postgres.sql").read_text()
    normalized = (
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
    )

    assert sql.count(normalized) >= 10
    assert sql.count("NOINHERIT") >= 10
    assert sql.count("NOLOGIN") >= 2
    assert sql.count("LOGIN NOINHERIT") >= 8
    assert "pg_auth_members" in sql
    assert "parent.rolname" in sql
    assert "member.rolname" in sql
    assert sql.count("REVOKE %I FROM %I") >= 2


def test_backup_and_restore_scripts_never_accept_embedded_credentials():
    backup = (ROOT / "ops/postgres/backup_shared_postgres.sh").read_text()
    restore = (ROOT / "ops/postgres/verify_restore.sh").read_text()

    assert "pg_dump" in backup
    assert "--format=custom" in backup
    assert "pg_dumpall" in backup
    assert "--globals-only" in backup
    assert "--no-role-passwords" in backup
    assert "pg_restore --list" in backup
    assert "PGPASSWORD" not in backup
    assert "postgresql://" not in backup
    assert "POSTGRES_LIVE_DATA_DIR" in backup
    assert "validate-backup-root" in backup
    assert "validate-scratch" in restore
    assert "ATLAS_SCRATCH_DATABASE" in restore
    assert "atlas.dump" in restore
    assert "globals.sql" in restore
    assert "sha256sum --check SHA256SUMS" in restore
    assert "pg_restore" in restore
    assert "--single-transaction" in restore
    assert "--exit-on-error" in restore
    assert "dropdb" in restore
    assert "PGPASSWORD" not in restore
    assert restore.index("trap cleanup EXIT") < restore.index("createdb --")


def test_import_wrapper_requires_postgres_and_explicit_verified_backup_gate():
    script = (ROOT / "ops/postgres/import_argus_legacy.sh").read_text()

    assert "ARGUS_DB_URL" in script
    assert "postgresql" in script
    assert "--apply-after-verified-backup" in script
    assert "LEGACY_SEARCH_DB_URL" in script
    assert "LEGACY_SESSION_DB_URL" in script
    assert "postgres_recovery.py\" import" in script
    assert "--search-source" not in script
    assert "--session-source" not in script
    assert "PGPASSWORD" not in script


def test_operator_runbook_leaves_production_acceptance_as_explicit_gates():
    runbook = (ROOT / "ops/postgres/README.md").read_text()

    assert "## Code-complete acceptance" in runbook
    assert "- [x] Provisioning is idempotent in disposable PostgreSQL 16" in runbook
    assert "- [x] Backup and restore artifacts contain no role password verifiers" in runbook
    assert "- [x] Schema-changing promotion fails closed on stale evidence" in runbook
    assert "## Production-only acceptance gates" in runbook
    assert "- [ ] Production tenant and role provisioning approved and completed" in runbook
    assert "- [ ] Production import reconciliation approved and completed" in runbook
    assert "- [ ] Production backup schedule approved and enabled" in runbook
    assert "- [ ] Production isolated restore approved and verified" in runbook
    assert "- [ ] Production cutover approved and completed" in runbook
    assert "Never run these scripts on the Mac development workstation" in runbook
