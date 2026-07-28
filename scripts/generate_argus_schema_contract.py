"""Regenerate the checked-in normalized PostgreSQL schema contract."""

from __future__ import annotations

import json

from argus.recovery.database import (
    SCHEMA_CONTRACT_PATH,
    build_argus_schema_contract,
)


def main() -> None:
    payload = build_argus_schema_contract()
    SCHEMA_CONTRACT_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
