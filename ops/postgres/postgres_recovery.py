#!/usr/bin/env python3
"""Credential-free operator entrypoint for shared PostgreSQL recovery."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from argus.recovery.database import verify_argus_database
from argus.recovery.evidence import evaluate_promotion_gate
from argus.recovery.importer import reconcile_import
from argus.recovery.operator import (
    validate_backup_root,
    validate_compatibility_alias,
    validate_scratch_database,
)
from argus.recovery.records import (
    prune_snapshots,
    record_backup,
    record_restore,
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

    alias = commands.add_parser("alias-check")
    alias.add_argument("--primary", default="homelab-postgres")
    alias.add_argument("--compatibility", default="atlas-postgres")

    prune = commands.add_parser("prune")
    prune.add_argument("--root", type=Path, required=True)
    prune.add_argument("--apply", action="store_true")

    backup = commands.add_parser("record-backup")
    backup.add_argument("--evidence", type=Path, required=True)
    backup.add_argument("--completed-at", required=True)
    backup.add_argument("--manifest-sha256", required=True)

    restore = commands.add_parser("record-restore")
    restore.add_argument("--evidence", type=Path, required=True)
    restore.add_argument("--schema-head", required=True)

    verify = commands.add_parser("verify-argus-db")
    verify.add_argument("--database", required=True)

    gate = commands.add_parser("promotion-gate")
    gate.add_argument("--evidence", type=Path, required=True)
    gate.add_argument("--schema-change", action="store_true")

    legacy = commands.add_parser("import")
    legacy.add_argument("--search-source", required=True)
    legacy.add_argument("--session-source", required=True)
    legacy.add_argument("--apply", action="store_true")
    return parser


def _target_repository():
    from argus.persistence.search_ledger import create_search_ledger_repository

    target = os.environ.get("ARGUS_DB_URL")
    if not target:
        raise ValueError("ARGUS_DB_URL must identify the pre-provisioned target")
    return create_search_ledger_repository(target, create_schema=False)


def run(arguments: list[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    if args.command == "validate-scratch":
        print(validate_scratch_database(args.database, tenant=args.tenant))
        return 0
    if args.command == "validate-backup-root":
        print(validate_backup_root(args.root, live_data=args.live_data))
        return 0
    if args.command == "alias-check":
        result = validate_compatibility_alias(
            args.primary,
            args.compatibility,
        )
    elif args.command == "prune":
        result = prune_snapshots(args.root, apply=args.apply)
    elif args.command == "record-backup":
        record_backup(
            args.evidence,
            completed_at=args.completed_at,
            manifest_sha256=args.manifest_sha256,
        )
        result = {"recorded": True}
    elif args.command == "record-restore":
        record_restore(args.evidence, schema_head=args.schema_head.strip())
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
        result = reconcile_import(
            search_source=args.search_source,
            session_source=args.session_source,
            repository=_target_repository(),
            apply=args.apply,
        )
    else:  # pragma: no cover - argparse enforces the command set
        raise AssertionError(args.command)
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> int:
    try:
        return run()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
