#!/bin/sh
set -eu

: "${ARGUS_BACKUP_SET:?set the immutable backup-set directory}"
: "${ARGUS_BACKUP_ROOT:?set the initialized backup root}"
: "${POSTGRES_LIVE_DATA_DIR:?set the absolute live PostgreSQL data directory}"
: "${ARGUS_RECOVERY_EVIDENCE:?set the recovery evidence JSON path}"
: "${SCRATCH_DATABASE:?set an explicit disposable database name}"
: "${ATLAS_SCRATCH_DATABASE:?set an explicit disposable Atlas database name}"

source_revision=${ARGUS_SOURCE_REVISION:-}
image_digest=${ARGUS_IMAGE_DIGEST:-}
identity_args=
[ -z "$source_revision" ] || identity_args="$identity_args --source-revision $source_revision"
[ -z "$image_digest" ] || identity_args="$identity_args --image-digest $image_digest"
[ "${ARGUS_METADATA_REGISTRY_COMPLETE:-}" = true ] || :
[ "${ARGUS_SCHEMA_CONTRACT_CLEAN:-}" = true ] || :
[ "${ARGUS_FORWARD_COMPATIBLE:-}" = true ] || :
[ "${ARGUS_ROLLBACK_PATH_HUMAN_APPROVED:-}" = true ] || :
[ "${ARGUS_METADATA_REGISTRY_COMPLETE:-}" = true ] && identity_args="$identity_args --metadata-registry-complete"
[ "${ARGUS_SCHEMA_CONTRACT_CLEAN:-}" = true ] && identity_args="$identity_args --schema-contract-clean"
[ "${ARGUS_FORWARD_COMPATIBLE:-}" = true ] && identity_args="$identity_args --forward-compatible"
[ "${ARGUS_ROLLBACK_PATH_HUMAN_APPROVED:-}" = true ] && identity_args="$identity_args --rollback-path-human-approved"
[ -z "${ARGUS_MIGRATION_RECEIPT:-}" ] || identity_args="$identity_args --migration-receipt $ARGUS_MIGRATION_RECEIPT"
[ -z "${ARGUS_OPERATOR_IDENTITY:-}" ] || identity_args="$identity_args --operator-identity $ARGUS_OPERATOR_IDENTITY"

postgres_container=${ARGUS_PG_CONTAINER:-}
postgres_exec_user=${ARGUS_PG_EXEC_USER:-postgres}
isolated_migration=${ARGUS_ISOLATED_RESTORE_MIGRATION:-false}
isolated_wrapper=${ARGUS_ISOLATED_RESTORE_WRAPPER:-}
isolated_run_label=${ARGUS_ISOLATED_RESTORE_RUN_LABEL:-}
case "$isolated_migration" in
    true|false) ;;
    *)
        echo "ARGUS_ISOLATED_RESTORE_MIGRATION must be true or false" >&2
        exit 2
        ;;
esac
if [ "$isolated_migration" = true ]; then
    command -v docker >/dev/null 2>&1 || {
        echo "docker is required for isolated migration" >&2
        exit 2
    }
    if [ -z "$postgres_container" ] || [ "$isolated_wrapper" != infra-v1 ]; then
        echo "migration requires the isolated Infra restore wrapper" >&2
        exit 2
    fi
    case "${PGHOST:-}" in
        127.0.0.1|::1) ;;
        *)
            echo "isolated migration requires a loopback PostgreSQL endpoint" >&2
            exit 2
            ;;
    esac
    case "$postgres_container" in
        *[!0-9a-f]*|'') echo "isolated migration requires a full container ID" >&2; exit 2 ;;
    esac
    [ "${#postgres_container}" -eq 64 ] || {
        echo "isolated migration requires a full container ID" >&2
        exit 2
    }
    printf '%s\n' "$isolated_run_label" | grep -Eq '^restore-[0-9]{8}T[0-9]{6}Z-[0-9]+$' || {
        echo "isolated migration requires an exact restore-run label" >&2
        exit 2
    }
    case "${PGPORT:-}" in
        ''|*[!0-9]*) echo "isolated migration requires a numeric PostgreSQL port" >&2; exit 2 ;;
    esac
    [ "$PGPORT" -ge 1 ] 2>/dev/null && [ "$PGPORT" -le 65535 ] 2>/dev/null || {
        echo "isolated migration PostgreSQL port is out of range" >&2
        exit 2
    }
    container_label=$(docker inspect --format '{{index .Config.Labels "com.khamel.argus.restore-run"}}' "$postgres_container")
    [ "$container_label" = "$isolated_run_label" ] || {
        echo "isolated migration container ownership is invalid" >&2
        exit 2
    }
    container_name=$(docker inspect --format '{{.Name}}' "$postgres_container")
    case "$container_name" in
        /argus-restore-*) ;;
        *) echo "isolated migration refuses a non-restore container" >&2; exit 2 ;;
    esac
    published_port=$(docker port "$postgres_container" 5432/tcp)
    [ "$published_port" = "127.0.0.1:$PGPORT" ] || {
        echo "isolated migration container port does not match the loopback endpoint" >&2
        exit 2
    }
fi
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
if [ -n "$postgres_container" ] && [ "$isolated_migration" != true ]; then
    python3 "$script_dir/postgres_recovery.py" record-restore \
        --evidence "$ARGUS_RECOVERY_EVIDENCE" \
        --backup-set "$ARGUS_BACKUP_SET" \
        --root "$ARGUS_BACKUP_ROOT" \
        --live-data "$POSTGRES_LIVE_DATA_DIR" \
        --argus-database "$SCRATCH_DATABASE" \
        --atlas-database "$ATLAS_SCRATCH_DATABASE" \
        --skip-migration \
        $identity_args
else
    python3 "$script_dir/postgres_recovery.py" record-restore \
        --evidence "$ARGUS_RECOVERY_EVIDENCE" \
        --backup-set "$ARGUS_BACKUP_SET" \
        --root "$ARGUS_BACKUP_ROOT" \
        --live-data "$POSTGRES_LIVE_DATA_DIR" \
        --argus-database "$SCRATCH_DATABASE" \
        --atlas-database "$ATLAS_SCRATCH_DATABASE" \
        $identity_args
fi
if [ "$isolated_migration" = true ]; then
    echo "ARGUS_ISOLATED_RESTORE_MIGRATION_VERIFIED=1"
fi
