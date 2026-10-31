# Argus #154 — existing host admission source reconciliation

Observed October 31, 2026 UTC. Refs [#154](https://github.com/Khamel83/argus/issues/154)
and parent [#151](https://github.com/Khamel83/argus/issues/151); neither issue is complete.

## Immutable sources and ownership

- Argus default: `13f54fcd185dab1ded863f67a9ca3085f015bbd4`.
- Infra default: `12a764a05d1d8498c0fffbe62aeff1efcf2a24f6`.
- [Full consolidated requirements](https://github.com/Khamel83/argus/issues/154#issuecomment-6116884960).
- [Canonical implementation ownership](https://github.com/Khamel83/infra/blob/12a764a05d1d8498c0fffbe62aeff1efcf2a24f6/docs/research/2026-10-20-fleet-execution-plan.md):
  Infra already owns the real guard; the retained callback-only Argus candidate
  must not become a second authority. Verify existing source/installed parity and
  remaining five-second qualification; repair only a demonstrated gap.
- [Guard](https://github.com/Khamel83/infra/blob/12a764a05d1d8498c0fffbe62aeff1efcf2a24f6/scripts/argus-scorecard-admission.py),
  [runner](https://github.com/Khamel83/infra/blob/12a764a05d1d8498c0fffbe62aeff1efcf2a24f6/scripts/argus-live-scorecard.py),
  [wrapper](https://github.com/Khamel83/infra/blob/12a764a05d1d8498c0fffbe62aeff1efcf2a24f6/scripts/argus-admit.sh),
  [operator contract](https://github.com/Khamel83/infra/blob/12a764a05d1d8498c0fffbe62aeff1efcf2a24f6/docs/operations/argus-promotion.md).

This supersedes an inference from the retained October 6 Argus handoff that
external startup wiring was still absent. The current Infra source already
wires the guard. Source presence is not installed parity or runtime acceptance.
GitHub #154 has no assignee and remains unable-to-claim/acceptance-pending.
The October 30 #151 execute reassessment is not an active-worker claim.
No inspected public record affirmatively establishes an active owner; native
claims/session state is unknown and no native checkout was inspected or changed.

## Exact execution path

The wrapper enters the root-owned guard before Docker operations, checks its
inherited descriptor, and registers the manifest-inspection container. Direct
runner `main()` uses that same guard unless a validated inherited slot exists.
`_run_attempt()` registers both exact Compose project names, then iterates
baseline and candidate serially. It calls `wait_capacity(settings())` immediately
before each `compose up`; durable evidence and zero spend are checked and the
baseline resources removed before candidate startup. One inherited kernel lock
covers the complete paired execution and cleanup, rather than separate
per-mode budgets. This source reconciliation introduces no runtime implementation.

## Consolidated criterion map

| Requirement | Current immutable source/test evidence | Remaining acceptance |
| --- | --- | --- |
| Same host budget before baseline/candidate resources | Guard `guarded()`, wrapper entry and runner `main()`; runner `_run_attempt()` serial sides; `test_separate_processes_share_one_slot`, `test_serial_execution_checks_receipts_before_teardown` | Installed source/config parity and actual-path receipt |
| Positive bounded default-one configuration | Guard `DEFAULTS`, `RANGES`, `settings()`; active_limit defaults 1, allowed 1–4; `test_configuration_is_bounded`, `test_configured_limit_allows_two_processes` | Actual protected configuration identity; no thresholds changed |
| Cross-process admission and worker termination | Slot flock is inherited by child; fixed queue locks release on exit; `test_child_holds_slot_after_guard_is_killed`, separate-process test | Operated crash/recovery evidence, not just process fixtures |
| Success/failure/cancel cleanup and safe reuse | Guard finally/recover verifies only recorded exact project labels; `test_failed_child_releases_slot`, `test_queued_cancellation_releases_waiter`, `test_crash_residue_cleanup_precedes_reuse`, `test_cleanup_failure_never_reports_completed` | Actual containers/networks/volumes absent after bounded interrupted run |
| Required CPU, memory, Docker objects, interfaces and active jobs | `capacity()` checks all required pressure metrics; slot count supplies active-job bound; parameterized `test_capacity_refusal_does_not_run_child` includes unavailable metrics | Real before/during/after observations and global pressure behavior |
| Recheck after waiting and before resource startup | `guarded()` probes while holding acquired slot; `wait_capacity()` rechecks before each side; `test_startup_pressure_queues_until_capacity_clears` | Actual startup/evidence-call trace |
| Bounded waiting/backoff, no partial startup | Fixed queue_limit defaults16, wait_seconds300, exponential delay1..8 seconds; queued refusal never starts child; cancellation test | Real queue transitions and no leaked resources |
| Content-free lifecycle/reason states | Guard emits admitted/queued/canceled/completed/failed/unavailable and bounded reason codes, not raw pressure observations | Natural lifecycle receipt; queued is the implementation spelling of waiting |
| Fenced cleanup rather than unsafe expiry | Inherited kernel lock persists with surviving child; exact fsynced project records survive parent crash; cleanup must succeed before reuse | No time-based forced lease expiry exists or is claimed; complete worker-death/reboot recovery still requires runtime qualification |
| Five-second Baywatch qualification | Runner `--qualification` retains actual workload checks but suppresses promotion recording; `test_runner_admits_only_after_residual_verification` tests qualification and residual failures | NOT performed: real concurrent Docker/Hermes/Homelab evidence calls, five-second maxima, pressure and complete cleanup receipts |
| Operations and Homelab coordination | Existing Infra operator contract documents implemented settings, queue, cleanup, rollback, qualifications | Actual Baywatch coordination/latency is not proven by documented intent or synthetic harness |

Do not replace the existing inherited-lock ownership model with the WIP PID-only
reclamation, or invent heartbeat/lease-expiry settings from the retained docs.
Current source uses bounded wait time, not the retained queue's six-attempt
contract. Full consolidated acceptance remains open where the actual operational
behavior has not been verified.

## Isolated verification performed

At the Infra revision above, both existing suites ran unchanged:

```sh
python -m pytest -q --noconftest \
  tests/test_argus_scorecard_admission.py \
  tests/test_argus_live_scorecard_contract.py
```

Result: **80 passed in 16.90 seconds**, Python 3.12. Tests used an isolated
immutable partial source snapshot, existing pytest dependencies, and
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; no repository conftest/plugin setup or full
native CI is claimed. A PATH-first Docker refusal executable prevented any
accidental real Docker invocation. Separate-process/kernel-lock tests used
temporary files and local Python children; Docker and pressure behavior was
mocked. All copied source/test bytes were checked against immutable Git blob IDs.
The captured output SHA-256 is
`93175f32db5631c44c12f14b64884ed08121bc56b6121668fdd69d5bfcc3c15b`.

No missing runtime-code assertion was demonstrated by this pass, so no runtime
code or tests were changed. This result does not qualify the real host.
Native CI, independent exact-head trusted review and ordinary merge of this
documentation candidate remain pending.

## Retained candidates preserved, not adopted wholesale

| Existing branch | Immutable retained head |
| --- | --- |
| g2k/issue-154 | c608de522914135aa3fb28d266d9ff119758b86e |
| g2k/issue-155 | 0a7afd93c55a28b1e4d9bf821dda9e628e984ef6 |
| g2k/issue-156 | 83bfbfe1b52094676016b311a4399bf833ac3a89 |
| g2k/issue-157 | e7faca4c00b404282fb744fac3547cd722ac2d9c |
| g2k/issue-158 | ac5712ca05505da92ea1103cc35cb7d68b3a5580 |

These WIP branches were each 46 commits behind the observed Argus main.
#154 and #155 add incompatible admission modules; #155 is process-local;
#156 queue callbacks do not integrate those modules; #158 documents different
settings. Their October 12 superseded closures explicitly are not implementation
completion. The earlier isolated retained tests remain useful comparison evidence,
not authority to install an alternative controller.
The proposal tree starts from current Argus main and contains documentation only;
the exact retained #154 head and all other WIP refs remain unchanged. This new
documentation-only branch starts from current main; it does not move any retained
worker branch. No native checkout, grant or stop record is reset.

## Next owner and gates

dot owns source reconciliation under existing #154; parent session owns any Git
publication. First independently inspect this candidate and refreshed source/claim
state; do not reuse Janitor PR224 or alter managed blocks. Required native CI and
exact-head trusted review precede normal merge. The authorized Infra/Homelab
operator owns fresh installed parity and the existing bounded real-path
qualification; no such execution authority is exercised here. Revisit when that
authorization/receipt is available or the owning source changes.

Keep #154 acceptance-pending and #151 open. No deployment, Docker workload,
SSH, provider/model call, billing change, new grant, queue, dashboard or scheduler
was introduced. Browser/network attestation remains fail-closed. Atlas remains
user-owned and untouched. A source merge cannot establish browser, provider,
production promotion or downstream acceptance.
