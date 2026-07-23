#!/bin/sh
set -eu

: "${ARGUS_BACKUP_ROOT:?set an absolute backup directory outside live data}"
: "${POSTGRES_LIVE_DATA_DIR:?set the absolute live PostgreSQL data directory}"
: "${ARGUS_RECOVERY_EVIDENCE:?set the recovery evidence JSON path}"

case "$ARGUS_BACKUP_ROOT" in
    /*) ;;
    *) echo "ARGUS_BACKUP_ROOT must be absolute" >&2; exit 2 ;;
esac
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 "$script_dir/postgres_recovery.py" validate-backup-root \
    --root "$ARGUS_BACKUP_ROOT" \
    --live-data "$POSTGRES_LIVE_DATA_DIR" >/dev/null
snapshot=$(date -u +%Y%m%dT%H%M%SZ)
stage=$(mktemp -d "$ARGUS_BACKUP_ROOT/.staging.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT HUP INT TERM

pg_dump --dbname=atlas --format=custom --file="$stage/atlas.dump"
pg_dump --dbname=argus --format=custom --file="$stage/argus.dump"
pg_dumpall --database=postgres --globals-only --no-role-passwords --file="$stage/globals.sql"
pg_restore --list "$stage/atlas.dump" >/dev/null
pg_restore --list "$stage/argus.dump" >/dev/null
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
python3 "$script_dir/postgres_recovery.py" retention-plan \
    --root "$ARGUS_BACKUP_ROOT" \
    --live-data "$POSTGRES_LIVE_DATA_DIR"
