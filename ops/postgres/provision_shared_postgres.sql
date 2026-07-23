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

REVOKE ALL ON DATABASE atlas FROM PUBLIC;
REVOKE ALL ON DATABASE argus FROM PUBLIC;
ALTER DATABASE atlas OWNER TO atlas_owner;
ALTER DATABASE argus OWNER TO argus_owner;
GRANT CONNECT ON DATABASE atlas
    TO atlas_migration, atlas_runtime, atlas_readonly, atlas_backup;
GRANT CONNECT ON DATABASE argus
    TO argus_migration, argus_runtime, argus_readonly, argus_backup;
REVOKE CONNECT ON DATABASE atlas
    FROM argus_migration, argus_runtime, argus_readonly, argus_backup;
REVOKE CONNECT ON DATABASE argus
    FROM atlas_migration, atlas_runtime, atlas_readonly, atlas_backup;

\connect atlas
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
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
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO atlas_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO atlas_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_migration IN SCHEMA public
    GRANT SELECT ON TABLES TO atlas_readonly, atlas_backup;

\connect argus
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
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
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO argus_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO argus_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE argus_migration IN SCHEMA public
    GRANT SELECT ON TABLES TO argus_readonly, argus_backup;
