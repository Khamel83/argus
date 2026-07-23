#!/bin/sh
set -eu

: "${ARGUS_DB_URL:?set the pre-provisioned Argus PostgreSQL target}"
: "${LEGACY_SEARCH_DB_URL:?set the read-only legacy search database URL}"
: "${LEGACY_SESSION_DB_URL:?set the read-only legacy session database URL}"

case "$ARGUS_DB_URL" in
    postgresql://*|postgresql+psycopg2://*) ;;
    *) echo "ARGUS_DB_URL must be PostgreSQL for this production import" >&2; exit 2 ;;
esac

apply=
case "${1:-}" in
    "") ;;
    --apply-after-verified-backup) apply=--apply ;;
    *) echo "usage: $0 [--apply-after-verified-backup]" >&2; exit 2 ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 "$script_dir/postgres_recovery.py" import \
    --search-source "$LEGACY_SEARCH_DB_URL" \
    --session-source "$LEGACY_SESSION_DB_URL" \
    $apply
