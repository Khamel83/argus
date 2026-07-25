#!/bin/sh
set -eu

: "${ARGUS_BACKUP_ROOT:?set an absolute backup directory outside live data}"
: "${POSTGRES_LIVE_DATA_DIR:?set the absolute live PostgreSQL data directory}"
: "${ARGUS_RECOVERY_EVIDENCE:?set the recovery evidence JSON path}"

postgres_container=${ARGUS_PG_CONTAINER:-}
postgres_exec_user=${ARGUS_PG_EXEC_USER:-postgres}
if [ -n "$postgres_container" ]; then
    command -v docker >/dev/null 2>&1 || {
        echo "docker is required when ARGUS_PG_CONTAINER is set" >&2
        exit 2
    }

    pg_dump_archive() {
        docker exec -u "$postgres_exec_user" "$postgres_container" \
            pg_dump "$@"
    }

    pg_dump_globals() {
        docker exec -u "$postgres_exec_user" "$postgres_container" \
            pg_dumpall "$@"
    }

    pg_restore_list() {
        docker exec -i -u "$postgres_exec_user" "$postgres_container" \
            pg_restore --list < "$1"
    }
else
    command -v pg_dump >/dev/null 2>&1 || {
        echo "pg_dump is required when ARGUS_PG_CONTAINER is unset" >&2
        exit 2
    }
    command -v pg_dumpall >/dev/null 2>&1 || {
        echo "pg_dumpall is required when ARGUS_PG_CONTAINER is unset" >&2
        exit 2
    }
    command -v pg_restore >/dev/null 2>&1 || {
        echo "pg_restore is required when ARGUS_PG_CONTAINER is unset" >&2
        exit 2
    }

    pg_dump_archive() {
        pg_dump "$@"
    }

    pg_dump_globals() {
        pg_dumpall "$@"
    }

    pg_restore_list() {
        pg_restore --list "$1"
    }
fi

case "$ARGUS_BACKUP_ROOT" in
    /*) ;;
    *) echo "ARGUS_BACKUP_ROOT must be absolute" >&2; exit 2 ;;
esac
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 "$script_dir/postgres_recovery.py" validate-backup-root \
    --root "$ARGUS_BACKUP_ROOT" \
    --live-data "$POSTGRES_LIVE_DATA_DIR" >/dev/null
command -v flock >/dev/null 2>&1 || {
    echo "flock is required for cooperative backup/retention locking" >&2
    exit 2
}
lock_file="$ARGUS_BACKUP_ROOT/.argus-shared-postgres-backup-root.json"
exec 9< "$lock_file"
flock -x 9
snapshot=$(date -u +%Y%m%dT%H%M%SZ)
stage=$(mktemp -d "$ARGUS_BACKUP_ROOT/.staging.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT HUP INT TERM

pg_dump_archive --dbname=atlas --format=custom > "$stage/atlas.dump"
pg_dump_archive --dbname=argus --format=custom > "$stage/argus.dump"
pg_dump_globals --database=postgres --globals-only --no-role-passwords > "$stage/globals.sql"
pg_restore_list "$stage/atlas.dump" >/dev/null
pg_restore_list "$stage/argus.dump" >/dev/null
(
    cd "$stage"
    sha256sum atlas.dump argus.dump globals.sql > SHA256SUMS
)
python3 "$script_dir/postgres_recovery.py" create-backup-manifest \
    --stage "$stage" \
    --root "$ARGUS_BACKUP_ROOT" \
    --live-data "$POSTGRES_LIVE_DATA_DIR" \
    --completed-at "$snapshot" >/dev/null

final="$ARGUS_BACKUP_ROOT/$snapshot"
test ! -e "$final"
mv -- "$stage" "$final"
trap - EXIT HUP INT TERM
python3 "$script_dir/postgres_recovery.py" record-backup \
    --evidence "$ARGUS_RECOVERY_EVIDENCE" \
    --backup-set "$final" \
    --root "$ARGUS_BACKUP_ROOT" \
    --live-data "$POSTGRES_LIVE_DATA_DIR"
flock -u 9
exec 9<&-
python3 "$script_dir/postgres_recovery.py" retention-plan \
    --root "$ARGUS_BACKUP_ROOT" \
    --live-data "$POSTGRES_LIVE_DATA_DIR"
