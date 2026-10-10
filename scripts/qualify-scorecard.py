#!/usr/bin/env python3
"""Run a bounded disposable scorecard/Baywatch qualification.

Commands are argv strings parsed without a shell.  Evidence commands must be
local Baywatch calls and use the JSON contract documented in
``docs/scorecards/qualification.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from argus.scorecard.qualification import (  # noqa: E402
    EVIDENCE_NAMES,
    QualificationConfig,
    QualificationError,
    default_configuration,
    parse_command,
    run_qualification,
)


def _command(value: str | None, *, label: str) -> tuple[str, ...] | None:
    return parse_command(value, label=label) if value else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--evidence-command",
        action="append",
        required=True,
        metavar="NAME=COMMAND",
        help="local Baywatch command; provide docker, hermes, and homelab exactly once",
    )
    parser.add_argument(
        "--baseline-command",
        help="scorecard argv; {output}, {run_id}, and {workload} are substituted",
    )
    parser.add_argument("--candidate-command", help="candidate scorecard argv")
    parser.add_argument(
        "--pressure-command",
        help="read-only pressure probe returning JSON; {phase} is substituted",
    )
    parser.add_argument(
        "--cleanup-command",
        help="disposable cleanup command returning JSON; {run_id} and {output} are substituted",
    )
    parser.add_argument("--scorecard-timeout", type=float, default=120.0)
    parser.add_argument("--scorecard-concurrency", type=int, default=1)
    parser.add_argument("--pressure-interval", type=float, default=0.25)
    args = parser.parse_args(argv)

    try:
        evidence: dict[str, tuple[str, ...]] = {}
        for item in args.evidence_command:
            if "=" not in item:
                raise QualificationError("--evidence-command must use NAME=COMMAND")
            name, command = item.split("=", 1)
            if name in evidence:
                raise QualificationError(f"duplicate evidence command: {name}")
            evidence[name] = parse_command(command, label=f"{name} evidence")
        if set(evidence) != set(EVIDENCE_NAMES):
            raise QualificationError("evidence commands must contain docker, hermes, and homelab")

        defaults = default_configuration(args.output)
        config = QualificationConfig(
            output=args.output,
            baseline_command=_command(args.baseline_command, label="baseline")
            or defaults.baseline_command,
            candidate_command=_command(args.candidate_command, label="candidate")
            or defaults.candidate_command,
            evidence_commands=evidence,
            pressure_command=_command(args.pressure_command, label="pressure"),
            cleanup_command=_command(args.cleanup_command, label="cleanup"),
            scorecard_timeout=args.scorecard_timeout,
            scorecard_concurrency=args.scorecard_concurrency,
            pressure_interval=args.pressure_interval,
            hermetic_defaults=args.baseline_command is None
            and args.candidate_command is None
            and args.cleanup_command is None,
        )
        report = run_qualification(config)
    except (OSError, QualificationError, ValueError) as exc:
        print(f"qualification configuration failed: {exc}", file=sys.stderr)
        return 2

    print(
        "qualification {status}: report={report} max_evidence_latency={latency:.3f}s "
        "max_active_scorecards={active}"
        .format(
            status=report["status"],
            report=args.output / "qualification.json",
            latency=report["max_evidence_latency_seconds"],
            active=report["queue"]["max_active"],
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
