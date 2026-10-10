# Disposable scorecard qualification

`qualify-scorecard.py` is a local qualification harness for scorecard admission
changes. It is not a deployment check and it never uses the production HTTP
authority or database.

## What it proves

The harness starts baseline and candidate workloads through the supplied
scorecard commands. They share one bounded in-process admission queue (default:
one active workload), so the second workload is queued before it can create a
Docker resource. Three local evidence commands run concurrently with that
activity:

- `docker` — Docker object and daemon evidence;
- `hermes` — Hermes evidence;
- `homelab` — Homelab runner-demand evidence.

Each evidence process has an unconditional five-second deadline. A timeout,
non-zero exit, or JSON response with `status` in `failed_closed`, `timeout`,
`error`, or `unavailable` fails the run. The report records the largest evidence
latency, queue transitions, maximum active scorecards, and read-only pressure
snapshots before, during, and after execution.

## Command contract

Commands are parsed as argv with `shlex`; no command is run through a shell.
Evidence commands must return exit code zero and MAY return a JSON object. A
JSON object with `status: "ok"` is conventional. A pressure command must return
a JSON object and receives `{phase}` as a substitution (`before`, `during`, or
`after`). It must include Docker object counts and interface/veth counts when
the local Baywatch adapter has those facts available.

The cleanup command receives `{run_id}` and `{output}`. It MUST remove only
resources labeled with `ARGUS_SCORECARD_QUALIFICATION_ID`, including scorecard
containers, networks, leases, and queue entries, then return either no output
or JSON such as:

```json
{"clean": true, "containers": 0, "networks": 0, "leases": 0, "queue_entries": 0}
```

A non-zero, timed-out, or non-clean cleanup fails the qualification. The
harness itself also requires zero active and pending queue entries after the
run. Interrupting the process kills active child processes, cancels waiting
work, runs cleanup, and writes an `interrupted` report.

## Disposable invocation

Use a temporary output directory and local Baywatch adapters. This example uses
the real hermetic scorecard path for both workloads; it performs no retrieval,
provider call, deployment, or database mutation:

```bash
uv run python scripts/qualify-scorecard.py \
  --output .artifacts/scorecard-qualification \
  --evidence-command 'docker=baywatch evidence docker --json' \
  --evidence-command 'hermes=baywatch evidence hermes --json' \
  --evidence-command 'homelab=baywatch evidence homelab-runner-demand --json' \
  --pressure-command 'baywatch scorecard-pressure --json --phase {phase}' \
  --cleanup-command 'baywatch scorecard-cleanup --run-id {run_id} --output {output} --json'
```

For an actual scorecard adapter, provide `--baseline-command` and
`--candidate-command`; both commands receive `{output}`, `{run_id}`, and
`{workload}` substitutions. The harness passes a sanitized environment with
`ARGUS_AUTOLOAD_DOTENV=false`, `ARGUS_ENV=development`,
`ARGUS_MCP_STANDALONE=true`, and an output-local SQLite URL. Authority URLs,
provider keys, database credentials, and other secret environment variables are
not inherited.

The run is successful only when `qualification.json` reports `status: "pass"`
and `checks.resources_clean`, `checks.evidence_within_deadline`, and
`checks.pressure_available` are true. Keep that JSON artifact with the local
scorecard evidence; it is not a production receipt.
