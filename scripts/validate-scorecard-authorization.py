#!/usr/bin/env python3
"""Validate and atomically consume one exact budgeted scorecard authorization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from argus.scorecard.authorization import (  # noqa: E402
    AuthorizationError,
    validate_and_consume_authorization,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--consumption-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = validate_and_consume_authorization(
            args.receipt,
            expected_sha256=args.expected_sha256,
            run_id=args.run_id,
            generation=args.generation,
            consumption_dir=args.consumption_dir,
        )
    except AuthorizationError as exc:
        print(f"budgeted scorecard authorization rejected: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "authorized": True,
                "receipt_id": receipt["receipt_id"],
                "run_id": receipt["run_id"],
                "generation": receipt["generation"],
                "maximum_tier": receipt["maximum_tier"],
                "call_count_cap": receipt["call_count_cap"],
                "cost_or_credit_cap": receipt["cost_or_credit_cap"],
                "permitted_providers": receipt["permitted_providers"],
                "one_time_credit_providers": receipt["one_time_credit_providers"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
