"""Regenerate the checked-in normalized PostgreSQL schema contract."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import create_engine

from argus.recovery.database import (
    SCHEMA_CONTRACT_PATH,
    build_argus_schema_contract,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Capture the checked contract from a disposable, fully migrated "
            "PostgreSQL database."
        )
    )
    parser.add_argument(
        "--database-url",
        required=True,
        help="Explicit postgresql:// or postgresql+psycopg2:// source URL",
    )
    args = parser.parse_args()
    if not args.database_url.startswith(
        ("postgresql://", "postgresql+psycopg2://")
    ):
        parser.error("--database-url must identify PostgreSQL")

    engine = create_engine(args.database_url)
    connection = engine.raw_connection()
    try:
        payload = build_argus_schema_contract(connection=connection)
    finally:
        connection.close()
        engine.dispose()
    SCHEMA_CONTRACT_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
