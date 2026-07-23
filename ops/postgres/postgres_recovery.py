#!/usr/bin/env python3
"""Credential-free operator entrypoint for shared PostgreSQL recovery."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from argus.recovery.artifacts import create_backup_manifest
from argus.recovery.database import verify_argus_database
from argus.recovery.evidence import evaluate_promotion_gate
from argus.recovery.importer import reconcile_import
from argus.recovery.operator import (
    validate_backup_root,
    validate_compatibility_alias,
    validate_credential_free_database_url,
    initialize_backup_root,
    validate_scratch_database,
)
from argus.recovery.records import (
    prune_snapshots,
    record_verified_backup,
    record_verified_restore,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    scratch = commands.add_parser("validate-scratch")
    scratch.add_argument("--database", required=True)
    scratch.add_argument("--tenant", choices=("argus", "atlas"), default="argus")

    backup_root = commands.add_parser("validate-backup-root")
    backup_root.add_argument("--root", type=Path, required=True)
    backup_root.add_argument("--live-data", type=Path, required=True)

    initialize = commands.add_parser("initialize-backup-root")
    initialize.add_argument("--root", type=Path, required=True)
    initialize.add_argument("--live-data", type=Path, required=True)

    alias = commands.add_parser("alias-check")
    alias.add_argument("--primary", default="homelab-postgres")
    alias.add_argument("--compatibility", default="atlas-postgres")

    prune = commands.add_parser(
        "prune",
        help="securely tombstone expired sets without deleting pathnames",
    )
    prune.add_argument("--root", type=Path, required=True)
    prune.add_argument("--live-data", type=Path, required=True)
    prune.add_argument("--apply", action="store_true")

    manifest = commands.add_parser("create-backup-manifest")
    manifest.add_argument("--stage", type=Path, required=True)
    manifest.add_argument("--root", type=Path, required=True)
    manifest.add_argument("--live-data", type=Path, required=True)
    manifest.add_argument("--completed-at", required=True)

    backup = commands.add_parser("record-backup")
    backup.add_argument("--evidence", type=Path, required=True)
    backup.add_argument("--backup-set", type=Path, required=True)
    backup.add_argument("--root", type=Path, required=True)
    backup.add_argument("--live-data", type=Path, required=True)

    restore = commands.add_parser("record-restore")
    restore.add_argument("--evidence", type=Path, required=True)
    restore.add_argument("--backup-set", type=Path, required=True)
    restore.add_argument("--root", type=Path, required=True)
    restore.add_argument("--live-data", type=Path, required=True)
    restore.add_argument("--argus-database", required=True)
    restore.add_argument("--atlas-database", required=True)

    verify = commands.add_parser("verify-argus-db")
    verify.add_argument("--database", required=True)

    gate = commands.add_parser("promotion-gate")
    gate.add_argument("--evidence", type=Path, required=True)
    gate.add_argument("--schema-change", action="store_true")

    legacy = commands.add_parser("import")
    legacy.add_argument("--apply", action="store_true")
    return parser


def _target_repository():
    from argus.persistence.search_ledger import create_search_ledger_repository

    target = os.environ.get("ARGUS_DB_URL")
    if not target:
        raise ValueError("ARGUS_DB_URL must identify the pre-provisioned target")
    validate_credential_free_database_url(target, allowed_database="argus")
    return create_search_ledger_repository(target, create_schema=False)


def run(arguments: list[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    if args.command == "validate-scratch":
        print(validate_scratch_database(args.database, tenant=args.tenant))
        return 0
    if args.command == "validate-backup-root":
        print(validate_backup_root(args.root, live_data=args.live_data))
        return 0
    if args.command == "initialize-backup-root":
        result = initialize_backup_root(args.root, live_data=args.live_data)
    elif args.command == "create-backup-manifest":
        result = create_backup_manifest(
            args.stage,
            root=args.root,
            live_data=args.live_data,
            completed_at=args.completed_at,
        )
    if args.command == "alias-check":
        result = validate_compatibility_alias(
            args.primary,
            args.compatibility,
        )
    elif args.command == "prune":
        result = prune_snapshots(
            args.root,
            live_data=args.live_data,
            apply=args.apply,
        )
    elif args.command == "record-backup":
        record_verified_backup(
            args.evidence,
            backup_set=args.backup_set,
            root=args.root,
            live_data=args.live_data,
        )
        result = {"recorded": True}
    elif args.command == "record-restore":
        record_verified_restore(
            args.evidence,
            backup_set=args.backup_set,
            root=args.root,
            live_data=args.live_data,
            argus_database=args.argus_database,
            atlas_database=args.atlas_database,
        )
        result = {"recorded": True}
    elif args.command == "verify-argus-db":
        result = verify_argus_database(args.database)
    elif args.command == "promotion-gate":
        result = evaluate_promotion_gate(
            args.evidence,
            schema_change=args.schema_change,
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["allowed"] else 1
    elif args.command == "import":
        search_source = os.environ.get("LEGACY_SEARCH_DB_URL", "")
        session_source = os.environ.get("LEGACY_SESSION_DB_URL", "")
        if not search_source or not session_source:
            raise ValueError("legacy source database URLs must be configured")
        validate_credential_free_database_url(search_source)
        validate_credential_free_database_url(session_source)
        result = reconcile_import(
            search_source=search_source,
            session_source=session_source,
            repository=_target_repository(),
            apply=args.apply,
        )
    elif args.command not in {"initialize-backup-root", "create-backup-manifest"}:
        raise AssertionError(args.command)
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> int:
    try:
        return run()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(
            f"error: operation failed ({type(error).__name__})",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
