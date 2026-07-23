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
mkdir -p "$ARGUS_BACKUP_ROOT"
stage=$(mktemp -d "$ARGUS_BACKUP_ROOT/.staging.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT HUP INT TERM

pg_dump --dbname="${ATLAS_DATABASE:-atlas}" --format=custom --file="$stage/atlas.dump"
pg_dump --dbname="${ARGUS_DATABASE:-argus}" --format=custom --file="$stage/argus.dump"
pg_dumpall --globals-only --no-role-passwords --file="$stage/globals.sql"
pg_restore --list "$stage/atlas.dump" >/dev/null
pg_restore --list "$stage/argus.dump" >/dev/null
(
    cd "$stage"
    sha256sum atlas.dump argus.dump globals.sql > SHA256SUMS
)

final="$ARGUS_BACKUP_ROOT/$snapshot"
test ! -e "$final"
mv -- "$stage" "$final"
trap - EXIT HUP INT TERM
manifest_sha=$(sha256sum "$final/SHA256SUMS" | awk '{print $1}')
python3 "$script_dir/postgres_recovery.py" record-backup \
    --evidence "$ARGUS_RECOVERY_EVIDENCE" \
    --completed-at "$snapshot" \
    --manifest-sha256 "$manifest_sha"
python3 "$script_dir/postgres_recovery.py" prune \
    --root "$ARGUS_BACKUP_ROOT" --apply
