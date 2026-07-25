#!/bin/sh
set -eu

: "${ARGUS_BACKUP_SET:?set the immutable backup-set directory}"
: "${ARGUS_BACKUP_ROOT:?set the initialized backup root}"
: "${POSTGRES_LIVE_DATA_DIR:?set the absolute live PostgreSQL data directory}"
: "${ARGUS_RECOVERY_EVIDENCE:?set the recovery evidence JSON path}"
: "${SCRATCH_DATABASE:?set an explicit disposable database name}"
: "${ATLAS_SCRATCH_DATABASE:?set an explicit disposable Atlas database name}"

postgres_container=${ARGUS_PG_CONTAINER:-}
postgres_exec_user=${ARGUS_PG_EXEC_USER:-postgres}
if [ -n "$postgres_container" ]; then
    command -v docker >/dev/null 2>&1 || {
        echo "docker is required when ARGUS_PG_CONTAINER is set" >&2
        exit 2
    }

    pg_restore_list() {
        docker exec -i -u "$postgres_exec_user" "$postgres_container" \
            pg_restore --list < "$1"
    }

    create_database() {
        docker exec -u "$postgres_exec_user" "$postgres_container" \
            createdb "$1"
    }

    drop_database() {
        docker exec -u "$postgres_exec_user" "$postgres_container" \
            dropdb --if-exists "$1"
    }

    restore_database() {
        database="$1"
        archive="$2"
        docker exec -i -u "$postgres_exec_user" "$postgres_container" \
            pg_restore --exit-on-error --single-transaction --no-owner \
            --no-privileges --dbname="$database" < "$archive"
    }
else
    command -v pg_restore >/dev/null 2>&1 || {
        echo "pg_restore is required when ARGUS_PG_CONTAINER is unset" >&2
        exit 2
    }
    command -v createdb >/dev/null 2>&1 || {
        echo "createdb is required when ARGUS_PG_CONTAINER is unset" >&2
        exit 2
    }
    command -v dropdb >/dev/null 2>&1 || {
        echo "dropdb is required when ARGUS_PG_CONTAINER is unset" >&2
        exit 2
    }

    pg_restore_list() {
        pg_restore --list "$1"
    }

    create_database() {
        createdb -- "$1"
    }

    drop_database() {
        dropdb --if-exists -- "$1"
    }

    restore_database() {
        database="$1"
        archive="$2"
        pg_restore --exit-on-error --single-transaction --no-owner \
            --no-privileges --dbname="$database" "$archive"
    }
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 "$script_dir/postgres_recovery.py" validate-scratch \
    --database "$SCRATCH_DATABASE" >/dev/null
python3 "$script_dir/postgres_recovery.py" validate-scratch \
    --tenant atlas --database "$ATLAS_SCRATCH_DATABASE" >/dev/null
test -f "$ARGUS_BACKUP_SET/argus.dump"
test -f "$ARGUS_BACKUP_SET/atlas.dump"
test -s "$ARGUS_BACKUP_SET/globals.sql"
test -f "$ARGUS_BACKUP_SET/SHA256SUMS"
(
    cd "$ARGUS_BACKUP_SET"
    sha256sum --check SHA256SUMS
)
pg_restore_list "$ARGUS_BACKUP_SET/argus.dump" >/dev/null
pg_restore_list "$ARGUS_BACKUP_SET/atlas.dump" >/dev/null
if grep -Eq 'SCRAM-SHA-256|PASSWORD[[:space:]]+['"'"'"]md5' \
    "$ARGUS_BACKUP_SET/globals.sql"; then
    echo "cluster globals contain a credential verifier" >&2
    exit 2
fi

argus_created=false
atlas_created=false
cleanup() {
    if [ "$argus_created" = true ]; then
        python3 "$script_dir/postgres_recovery.py" validate-scratch \
            --database "$SCRATCH_DATABASE" >/dev/null
        drop_database "$SCRATCH_DATABASE" || true
    fi
    if [ "$atlas_created" = true ]; then
        python3 "$script_dir/postgres_recovery.py" validate-scratch \
            --tenant atlas --database "$ATLAS_SCRATCH_DATABASE" >/dev/null
        drop_database "$ATLAS_SCRATCH_DATABASE" || true
    fi
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

create_database "$SCRATCH_DATABASE"
argus_created=true
create_database "$ATLAS_SCRATCH_DATABASE"
atlas_created=true

restore_database "$SCRATCH_DATABASE" "$ARGUS_BACKUP_SET/argus.dump"
restore_database "$ATLAS_SCRATCH_DATABASE" "$ARGUS_BACKUP_SET/atlas.dump"
if [ -n "$postgres_container" ]; then
    python3 "$script_dir/postgres_recovery.py" record-restore \
        --evidence "$ARGUS_RECOVERY_EVIDENCE" \
        --backup-set "$ARGUS_BACKUP_SET" \
        --root "$ARGUS_BACKUP_ROOT" \
        --live-data "$POSTGRES_LIVE_DATA_DIR" \
        --argus-database "$SCRATCH_DATABASE" \
        --atlas-database "$ATLAS_SCRATCH_DATABASE" \
        --skip-migration
else
    python3 "$script_dir/postgres_recovery.py" record-restore \
        --evidence "$ARGUS_RECOVERY_EVIDENCE" \
        --backup-set "$ARGUS_BACKUP_SET" \
        --root "$ARGUS_BACKUP_ROOT" \
        --live-data "$POSTGRES_LIVE_DATA_DIR" \
        --argus-database "$SCRATCH_DATABASE" \
        --atlas-database "$ATLAS_SCRATCH_DATABASE"
fi
