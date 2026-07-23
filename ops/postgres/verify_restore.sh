#!/bin/sh
set -eu

: "${ARGUS_BACKUP_SET:?set the immutable backup-set directory}"
: "${ARGUS_RECOVERY_EVIDENCE:?set the recovery evidence JSON path}"
: "${SCRATCH_DATABASE:?set an explicit disposable database name}"
: "${ATLAS_SCRATCH_DATABASE:?set an explicit disposable Atlas database name}"

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
pg_restore --list "$ARGUS_BACKUP_SET/argus.dump" >/dev/null
pg_restore --list "$ARGUS_BACKUP_SET/atlas.dump" >/dev/null
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
        dropdb --if-exists -- "$SCRATCH_DATABASE" || true
    fi
    if [ "$atlas_created" = true ]; then
        python3 "$script_dir/postgres_recovery.py" validate-scratch \
            --tenant atlas --database "$ATLAS_SCRATCH_DATABASE" >/dev/null
        dropdb --if-exists -- "$ATLAS_SCRATCH_DATABASE" || true
    fi
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

createdb -- "$SCRATCH_DATABASE"
argus_created=true
createdb -- "$ATLAS_SCRATCH_DATABASE"
atlas_created=true

pg_restore --exit-on-error --single-transaction --no-owner --no-privileges \
    --dbname="$SCRATCH_DATABASE" "$ARGUS_BACKUP_SET/argus.dump"
pg_restore --exit-on-error --single-transaction --no-owner --no-privileges \
    --dbname="$ATLAS_SCRATCH_DATABASE" "$ARGUS_BACKUP_SET/atlas.dump"
atlas_tables=$(psql --dbname="$ATLAS_SCRATCH_DATABASE" --tuples-only --no-align \
    --command="SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
test "$atlas_tables" -gt 0
ARGUS_DB_URL="postgresql+psycopg2:///$SCRATCH_DATABASE" alembic upgrade head
python3 "$script_dir/postgres_recovery.py" verify-argus-db \
    --database "$SCRATCH_DATABASE"
schema_head=$(psql --dbname="$SCRATCH_DATABASE" --tuples-only --no-align \
    --command='SELECT version_num FROM alembic_version')
python3 "$script_dir/postgres_recovery.py" record-restore \
    --evidence "$ARGUS_RECOVERY_EVIDENCE" \
    --schema-head "$schema_head"
