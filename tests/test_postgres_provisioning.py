"""Real PostgreSQL contract tests for the shared-cluster role allowlist."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url


ROOT = Path(__file__).parents[1]
DATABASE_URL = os.getenv("ARGUS_TEST_POSTGRES_URL", "")
DISPOSABLE_OPT_IN = (
    os.getenv("ARGUS_TEST_ALLOW_PROVISIONING") == "disposable-only"
)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not DISPOSABLE_OPT_IN or shutil.which("psql") is None,
    reason="requires an explicitly disposable PostgreSQL server and psql",
)

MANAGED_ROLES = (
    "atlas_owner",
    "atlas_migration",
    "atlas_runtime",
    "atlas_readonly",
    "atlas_backup",
    "argus_owner",
    "argus_migration",
    "argus_runtime",
    "argus_readonly",
    "argus_backup",
)


def _psql(
    sql: str | None = None,
    *,
    database: str = "postgres",
    file: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    url = make_url(DATABASE_URL)
    environment = os.environ.copy()
    if url.password:
        environment["PGPASSWORD"] = url.password
    command = [
        "psql",
        "--no-psqlrc",
        "--set",
        "ON_ERROR_STOP=1",
        "--host",
        str(url.host or "127.0.0.1"),
        "--port",
        str(url.port or 5432),
        "--username",
        str(url.username or "postgres"),
        "--dbname",
        database,
    ]
    if file is not None:
        command.extend(["--file", str(file)])
    else:
        command.extend(["--tuples-only", "--no-align", "--command", sql or ""])
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        env=environment,
    )


def _cleanup() -> None:
    roles = ", ".join(MANAGED_ROLES)
    _psql(
        f"""
        DROP DATABASE IF EXISTS atlas WITH (FORCE);
        DROP DATABASE IF EXISTS argus WITH (FORCE);
        DROP ROLE IF EXISTS {roles};
        """
    )


def test_provisioning_scrubs_poisoned_acl_and_rejects_object_ownership():
    script = ROOT / "ops/postgres/provision_shared_postgres.sql"
    _cleanup()
    try:
        _psql(file=script)
        _psql(file=script)

        _psql(
            """
            CREATE TABLE public.poisoned_owner (id integer);
            ALTER TABLE public.poisoned_owner OWNER TO atlas_runtime;
            """,
            database="atlas",
        )
        poisoned = _psql(file=script, check=False)
        assert poisoned.returncode != 0
        assert "unexpected managed-role object owner in atlas" in poisoned.stderr
        _psql("DROP TABLE public.poisoned_owner", database="atlas")

        _psql(
            """
            SET ROLE atlas_migration;
            CREATE TABLE public.acl_probe (id bigserial PRIMARY KEY);
            CREATE FUNCTION public.acl_probe_fn()
                RETURNS integer LANGUAGE sql AS 'SELECT 1';
            RESET ROLE;
            SET ROLE atlas_owner;
            CREATE SCHEMA private_probe;
            CREATE TABLE private_probe.acl_probe (id bigserial PRIMARY KEY);
            CREATE FUNCTION private_probe.acl_probe_fn()
                RETURNS integer LANGUAGE sql AS 'SELECT 1';
            RESET ROLE;
            GRANT ALL PRIVILEGES ON DATABASE atlas TO argus_runtime;
            GRANT ALL PRIVILEGES ON SCHEMA public
                TO argus_runtime, atlas_readonly;
            GRANT ALL PRIVILEGES ON TABLE public.acl_probe
                TO argus_runtime, atlas_readonly;
            GRANT ALL PRIVILEGES ON SEQUENCE public.acl_probe_id_seq
                TO argus_runtime, atlas_readonly;
            GRANT ALL PRIVILEGES ON FUNCTION public.acl_probe_fn()
                TO argus_runtime, atlas_readonly;
            GRANT ALL PRIVILEGES ON SCHEMA private_probe TO argus_runtime;
            GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA private_probe
                TO argus_runtime;
            GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA private_probe
                TO argus_runtime;
            GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA private_probe
                TO argus_runtime;
            """,
            database="atlas",
        )
        _psql(file=script)

        result = _psql(
            """
            SELECT
                has_database_privilege('argus_runtime', 'atlas', 'CONNECT'),
                has_schema_privilege('argus_runtime', 'public', 'USAGE'),
                has_table_privilege(
                    'argus_runtime', 'public.acl_probe', 'SELECT'
                ),
                has_sequence_privilege(
                    'argus_runtime', 'public.acl_probe_id_seq', 'USAGE'
                ),
                has_function_privilege(
                    'argus_runtime', 'public.acl_probe_fn()', 'EXECUTE'
                ),
                has_table_privilege(
                    'atlas_readonly', 'public.acl_probe', 'SELECT'
                ),
                has_table_privilege(
                    'atlas_readonly', 'public.acl_probe', 'INSERT'
                ),
                has_function_privilege(
                    'atlas_readonly', 'public.acl_probe_fn()', 'EXECUTE'
                ),
                has_table_privilege(
                    'atlas_runtime', 'public.acl_probe', 'INSERT'
                ),
                has_function_privilege(
                    'atlas_runtime', 'public.acl_probe_fn()', 'EXECUTE'
                ),
                has_schema_privilege(
                    'argus_runtime', 'private_probe', 'USAGE'
                ),
                has_table_privilege(
                    'argus_runtime', 'private_probe.acl_probe', 'SELECT'
                ),
                has_sequence_privilege(
                    'argus_runtime',
                    'private_probe.acl_probe_id_seq',
                    'USAGE'
                ),
                has_function_privilege(
                    'argus_runtime',
                    'private_probe.acl_probe_fn()',
                    'EXECUTE'
                );
            """,
            database="atlas",
        )
        assert result.stdout.strip() == (
            "f|f|f|f|f|t|f|f|t|t|f|f|f|f"
        )
    finally:
        _cleanup()
