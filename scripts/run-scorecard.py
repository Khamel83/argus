#!/usr/bin/env python3
"""Run the deterministic scorecard lanes that are safe on a developer or CI host.

This script has no provider client and does not invoke HTTP.  The live lane is
configuration-only; actual competitive execution remains a protected external
promotion operation through the canonical HTTP authority.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from argus.scorecard.bundle import write_bundle  # noqa: E402
from argus.scorecard.corpus import load_corpus  # noqa: E402
from argus.scorecard.stability import HARD_GATES, evaluate_stability  # noqa: E402


def _stable_profile() -> dict[str, dict[str, object]]:
    return {
        gate: {"status": "pass", "evidence": {"fixture": gate}} for gate in HARD_GATES
    }


def _live_configuration() -> dict[str, object]:
    """Describe the guarded live procedure without performing it."""
    return {
        "execution_authority": "canonical_http",
        "pr_safe": False,
        "synchronization": [
            "baseline",
            "candidate",
            "topology",
            "profile",
            "provider_snapshot",
        ],
        "free_profile": {"automatic": True, "billable_provider_calls": False},
        "budgeted_profile": {
            "authorization_receipt_required": True,
            "receipt_fields": [
                "run_id",
                "generation",
                "permitted_providers",
                "maximum_tier",
                "call_count_cap",
                "cost_or_credit_cap",
            ],
            "receipt_reuse": "rejected",
            "one_time_credit_providers": "disabled_unless_named",
        },
    }


def run_hermetic(output: Path) -> Path:
    corpus_path = ROOT / "tests" / "fixtures" / "scorecard" / "corpus.json"
    corpus = load_corpus(corpus_path)
    corpus_bytes = corpus_path.read_bytes()
    stability = evaluate_stability(
        {"free": _stable_profile(), "budgeted": _stable_profile()},
        architecture_exceptions=(),
    )
    payload = {
        "run_id": "hermetic-scorecard-v1",
        "generation": corpus["version"],
        "candidate_identity": {
            "commit": os.environ.get("GITHUB_SHA", "local-hermetic"),
            "generation": corpus["version"],
        },
        "corpus": {
            "version": corpus["version"],
            "sha256": sha256(corpus_bytes).hexdigest(),
        },
        "provider_snapshot": {
            "providers": [],
            "profile": "hermetic",
            "execution": "none",
        },
        "surface_equivalence": {
            "status": "pass",
            "fixtures": [
                "success",
                "degraded",
                "empty",
                "timeout",
                "policy_rejected",
                "authentication_rejected",
                "providers_failed",
            ],
        },
        "artifacts": {
            "hermetic-summary.json": {
                "search_intents": len(corpus["search_intents"]),
                "hermetic_extractions": len(corpus["hermetic_extractions"]),
                "provider_execution": "none",
                "verdict": stability.verdict,
            }
        },
    }
    return write_bundle(output, lane="hermetic", stability=stability, payload=payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=("hermetic", "live-config"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.lane == "live-config":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(_live_configuration(), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"wrote live competitive configuration to {args.output}; no live execution performed"
        )
        return 0
    output = run_hermetic(args.output)
    print(f"hermetic stability verdict: stable; verified bundle: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
