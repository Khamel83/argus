\set ON_ERROR_STOP on

-- This script creates identities only. Operators provision authentication
-- through libpq-managed secrets after review; credentials never belong here.
SELECT format('CREATE ROLE %I NOLOGIN', role_name)
FROM (VALUES ('atlas_owner'), ('argus_owner')) AS roles(role_name)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = role_name)
\gexec

SELECT format('CREATE ROLE %I LOGIN NOINHERIT', role_name)
FROM (
    VALUES
        ('atlas_migration'), ('atlas_runtime'), ('atlas_readonly'), ('atlas_backup'),
        ('argus_migration'), ('argus_runtime'), ('argus_readonly'), ('argus_backup')
) AS roles(role_name)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = role_name)
\gexec

-- Existing same-named roles are untrusted input. Normalize every capability
-- before any database or schema grant, while leaving authentication material
-- to the operator's external secret-management step.
ALTER ROLE atlas_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE argus_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE atlas_migration LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE atlas_runtime LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE atlas_readonly LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE atlas_backup LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE argus_migration LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE argus_runtime LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE argus_readonly LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE argus_backup LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- Strip both directions of inherited membership. This prevents a poisoned
-- pre-existing role from retaining another role's privileges or granting a
-- tenant role to an unexpected principal.
SELECT format('REVOKE %I FROM %I', parent.rolname, member.rolname)
FROM pg_auth_members membership
JOIN pg_roles parent ON parent.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE member.rolname IN (
    'atlas_owner', 'atlas_migration', 'atlas_runtime', 'atlas_readonly', 'atlas_backup',
    'argus_owner', 'argus_migration', 'argus_runtime', 'argus_readonly', 'argus_backup'
)
\gexec
SELECT format('REVOKE %I FROM %I', parent.rolname, member.rolname)
FROM pg_auth_members membership
JOIN pg_roles parent ON parent.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE parent.rolname IN (
    'atlas_owner', 'atlas_migration', 'atlas_runtime', 'atlas_readonly', 'atlas_backup',
    'argus_owner', 'argus_migration', 'argus_runtime', 'argus_readonly', 'argus_backup'
)
\gexec

SELECT 'CREATE DATABASE atlas OWNER atlas_owner'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'atlas')
\gexec
SELECT 'CREATE DATABASE argus OWNER argus_owner'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'argus')
\gexec

DO $database_ownership$
DECLARE
    poisoned text;
BEGIN
    SELECT format('database %I owned by %I', database_name, owner.rolname)
    INTO poisoned
    FROM (
        VALUES ('atlas', 'atlas_owner'), ('argus', 'argus_owner')
    ) AS expected(database_name, owner_name)
    JOIN pg_database database ON database.datname = expected.database_name
    JOIN pg_roles owner ON owner.oid = database.datdba
    WHERE owner.rolname IN (
        'atlas_owner', 'atlas_migration', 'atlas_runtime', 'atlas_readonly', 'atlas_backup',
        'argus_owner', 'argus_migration', 'argus_runtime', 'argus_readonly', 'argus_backup'
    )
      AND owner.rolname <> expected.owner_name
    LIMIT 1;
    IF poisoned IS NOT NULL THEN
        RAISE EXCEPTION 'unexpected managed-role database owner: %', poisoned;
    END IF;
END
$database_ownership$;

REVOKE ALL PRIVILEGES ON DATABASE atlas FROM PUBLIC;
REVOKE ALL PRIVILEGES ON DATABASE argus FROM PUBLIC;
REVOKE ALL PRIVILEGES ON DATABASE atlas FROM
    atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
    argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
REVOKE ALL PRIVILEGES ON DATABASE argus FROM
    atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
    argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
ALTER DATABASE atlas OWNER TO atlas_owner;
ALTER DATABASE argus OWNER TO argus_owner;
GRANT ALL PRIVILEGES ON DATABASE atlas TO atlas_owner;
GRANT ALL PRIVILEGES ON DATABASE argus TO argus_owner;
GRANT CONNECT ON DATABASE atlas
    TO atlas_migration, atlas_runtime, atlas_readonly, atlas_backup;
GRANT CONNECT ON DATABASE argus
    TO argus_migration, argus_runtime, argus_readonly, argus_backup;
REVOKE CONNECT ON DATABASE atlas
    FROM argus_migration, argus_runtime, argus_readonly, argus_backup;
REVOKE CONNECT ON DATABASE argus
    FROM atlas_migration, atlas_runtime, atlas_readonly, atlas_backup;

\connect atlas
DO $ownership$
DECLARE
    poisoned text;
BEGIN
    -- Supported tenant-owned classes: schemas; relations (tables,
    -- partitions, views, materialized views, sequences, foreign tables);
    -- routines; and user-defined types (including enums and domains).
    SELECT format('%s %I.%I owned by %I', object_kind, schema_name, object_name, owner_name)
    INTO poisoned
    FROM (
        SELECT 'schema' AS object_kind, n.nspname AS schema_name,
               n.nspname AS object_name, owner.rolname AS owner_name
        FROM pg_namespace n
        JOIN pg_roles owner ON owner.oid = n.nspowner
        UNION ALL
        SELECT 'relation', n.nspname, c.relname, owner.rolname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_roles owner ON owner.oid = c.relowner
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
        UNION ALL
        SELECT 'routine', n.nspname, p.proname, owner.rolname
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_roles owner ON owner.oid = p.proowner
        UNION ALL
        SELECT 'type', n.nspname, t.typname, owner.rolname
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        JOIN pg_roles owner ON owner.oid = t.typowner
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
    ) AS objects
    WHERE owner_name IN (
        'atlas_owner', 'atlas_migration', 'atlas_runtime', 'atlas_readonly', 'atlas_backup',
        'argus_owner', 'argus_migration', 'argus_runtime', 'argus_readonly', 'argus_backup'
    )
      AND owner_name NOT IN ('atlas_owner', 'atlas_migration')
    LIMIT 1;
    IF poisoned IS NOT NULL THEN
        RAISE EXCEPTION 'unexpected managed-role object owner in atlas: %', poisoned;
    END IF;
END
$ownership$;

SELECT format(
    'REVOKE ALL PRIVILEGES ON SCHEMA %I FROM atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup, argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup',
    nspname
)
FROM pg_namespace
WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I FROM PUBLIC, atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup, argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup',
    nspname
)
FROM pg_namespace
WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA %I FROM PUBLIC, atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup, argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup',
    nspname
)
FROM pg_namespace
WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA %I FROM PUBLIC, atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup, argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup',
    nspname
)
FROM pg_namespace
WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON %s %I.%I FROM PUBLIC, atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup, argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup',
    CASE WHEN t.typtype = 'd' THEN 'DOMAIN' ELSE 'TYPE' END,
    n.nspname,
    t.typname
)
FROM pg_type t
JOIN pg_namespace n ON n.oid = t.typnamespace
LEFT JOIN pg_class c ON c.oid = t.typrelid
WHERE n.nspname !~ '^pg_'
  AND n.nspname <> 'information_schema'
  AND t.typelem = 0
  AND (t.typrelid = 0 OR c.relkind = 'c')
\gexec

REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM
    atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
    argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM
    PUBLIC, atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
    argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM
    PUBLIC, atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
    argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM
    atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
    argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
GRANT USAGE, CREATE ON SCHEMA public TO atlas_migration;
GRANT USAGE ON SCHEMA public TO atlas_runtime, atlas_readonly, atlas_backup;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
    TO atlas_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA public
    TO atlas_readonly, atlas_backup;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public
    TO atlas_runtime;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public
    TO atlas_readonly, atlas_backup;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO atlas_runtime;
SELECT format(
    'GRANT USAGE ON %s %I.%I TO atlas_runtime, atlas_readonly, atlas_backup',
    CASE WHEN t.typtype = 'd' THEN 'DOMAIN' ELSE 'TYPE' END,
    n.nspname,
    t.typname
)
FROM pg_type t
JOIN pg_namespace n ON n.oid = t.typnamespace
LEFT JOIN pg_class c ON c.oid = t.typrelid
WHERE n.nspname = 'public'
  AND t.typelem = 0
  AND (t.typrelid = 0 OR c.relkind = 'c')
\gexec
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM
        atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
        argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM
        atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
        argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON FUNCTIONS FROM
        atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
        argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TYPES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TYPES FROM
        atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
        argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO atlas_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO atlas_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    GRANT SELECT ON TABLES TO atlas_readonly, atlas_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO atlas_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    GRANT USAGE ON TYPES TO atlas_runtime, atlas_readonly, atlas_backup;

\connect argus
DO $ownership$
DECLARE
    poisoned text;
BEGIN
    -- Keep this class list in lockstep with the Atlas ownership check above.
    SELECT format('%s %I.%I owned by %I', object_kind, schema_name, object_name, owner_name)
    INTO poisoned
    FROM (
        SELECT 'schema' AS object_kind, n.nspname AS schema_name,
               n.nspname AS object_name, owner.rolname AS owner_name
        FROM pg_namespace n
        JOIN pg_roles owner ON owner.oid = n.nspowner
        UNION ALL
        SELECT 'relation', n.nspname, c.relname, owner.rolname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_roles owner ON owner.oid = c.relowner
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
        UNION ALL
        SELECT 'routine', n.nspname, p.proname, owner.rolname
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_roles owner ON owner.oid = p.proowner
        UNION ALL
        SELECT 'type', n.nspname, t.typname, owner.rolname
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        JOIN pg_roles owner ON owner.oid = t.typowner
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
    ) AS objects
    WHERE owner_name IN (
        'atlas_owner', 'atlas_migration', 'atlas_runtime', 'atlas_readonly', 'atlas_backup',
        'argus_owner', 'argus_migration', 'argus_runtime', 'argus_readonly', 'argus_backup'
    )
      AND owner_name NOT IN ('argus_owner', 'argus_migration')
    LIMIT 1;
    IF poisoned IS NOT NULL THEN
        RAISE EXCEPTION 'unexpected managed-role object owner in argus: %', poisoned;
    END IF;
END
$ownership$;

SELECT format(
    'REVOKE ALL PRIVILEGES ON SCHEMA %I FROM atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup, argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup',
    nspname
)
FROM pg_namespace
WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I FROM PUBLIC, atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup, argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup',
    nspname
)
FROM pg_namespace
WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA %I FROM PUBLIC, atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup, argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup',
    nspname
)
FROM pg_namespace
WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA %I FROM PUBLIC, atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup, argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup',
    nspname
)
FROM pg_namespace
WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
\gexec
SELECT format(
    'REVOKE ALL PRIVILEGES ON %s %I.%I FROM PUBLIC, atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup, argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup',
    CASE WHEN t.typtype = 'd' THEN 'DOMAIN' ELSE 'TYPE' END,
    n.nspname,
    t.typname
)
FROM pg_type t
JOIN pg_namespace n ON n.oid = t.typnamespace
LEFT JOIN pg_class c ON c.oid = t.typrelid
WHERE n.nspname !~ '^pg_'
  AND n.nspname <> 'information_schema'
  AND t.typelem = 0
  AND (t.typrelid = 0 OR c.relkind = 'c')
\gexec

REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM
    atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
    argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM
    PUBLIC, atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
    argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM
    PUBLIC, atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
    argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM
    atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
    argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
GRANT USAGE, CREATE ON SCHEMA public TO argus_migration;
GRANT USAGE ON SCHEMA public TO argus_runtime, argus_readonly, argus_backup;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
    TO argus_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA public
    TO argus_readonly, argus_backup;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public
    TO argus_runtime;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public
    TO argus_readonly, argus_backup;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO argus_runtime;
SELECT format(
    'GRANT USAGE ON %s %I.%I TO argus_runtime, argus_readonly, argus_backup',
    CASE WHEN t.typtype = 'd' THEN 'DOMAIN' ELSE 'TYPE' END,
    n.nspname,
    t.typname
)
FROM pg_type t
JOIN pg_namespace n ON n.oid = t.typnamespace
LEFT JOIN pg_class c ON c.oid = t.typrelid
WHERE n.nspname = 'public'
  AND t.typelem = 0
  AND (t.typrelid = 0 OR c.relkind = 'c')
\gexec
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM
        atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
        argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM
        atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
        argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON FUNCTIONS FROM
        atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
        argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TYPES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TYPES FROM
        atlas_owner, atlas_migration, atlas_runtime, atlas_readonly, atlas_backup,
        argus_owner, argus_migration, argus_runtime, argus_readonly, argus_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO argus_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO argus_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    GRANT SELECT ON TABLES TO argus_readonly, argus_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO argus_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    GRANT USAGE ON TYPES TO argus_runtime, argus_readonly, argus_backup;
