"""Regenerate the checked-in normalized PostgreSQL schema contract."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import create_engine

from argus.recovery.database import (
    SCHEMA_CONTRACT_PATHS,
    build_argus_schema_contract,
)


def generate_schema_contract(
    *,
    database_url: str,
    schema_head: str,
    check: bool = False,
):
    if not database_url.startswith(
        ("postgresql://", "postgresql+psycopg2://")
    ):
        raise ValueError("database URL must identify PostgreSQL")
    try:
        output_path = SCHEMA_CONTRACT_PATHS[schema_head]
    except KeyError as error:
        raise ValueError(f"unsupported schema head: {schema_head}") from error

    engine = create_engine(database_url)
    connection = engine.raw_connection()
    try:
        payload = build_argus_schema_contract(connection=connection)
    finally:
        connection.close()
        engine.dispose()
    if payload.get("schema_head") != schema_head:
        raise RuntimeError(
            "PostgreSQL source schema head does not match --schema-head"
        )
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if check:
        if not output_path.is_file() or output_path.read_text(
            encoding="utf-8"
        ) != rendered:
            raise RuntimeError(
                f"schema contract is out of date: {output_path}"
            )
    else:
        output_path.write_text(rendered, encoding="utf-8")
    return output_path


def main(argv: list[str] | None = None) -> None:
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
    parser.add_argument(
        "--schema-head",
        required=True,
        choices=sorted(SCHEMA_CONTRACT_PATHS),
        help="Registered Alembic head and matching checked contract path",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the checked contract differs instead of writing it",
    )
    args = parser.parse_args(argv)
    try:
        generate_schema_contract(
            database_url=args.database_url,
            schema_head=args.schema_head,
            check=args.check,
        )
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
