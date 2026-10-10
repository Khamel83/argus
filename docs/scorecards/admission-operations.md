# Scorecard admission and Homelab operations

Status: operational contract for the host-side scorecard admission coordinator

This guide covers the ephemeral Docker work used by a live baseline/candidate
scorecard run. It does not change the scorecard verdict rules in
[stability-competitive.md](stability-competitive.md).

The coordinator and its settings are the deliverable of the parent issue #151
children: the shared admission budget (#154), the host/Docker pressure gate
(#155), the queue and recovery semantics (#156), and the bounded qualification
harness (#157). The setting names, defaults, and ranges below are the normative
contract for that work, not a description of code already shipped in this
repository. This repository's only scorecard execution today is the
`scripts/run-scorecard.py` diagnostic runner; it creates no Docker resources.

That runner's `--lane hermetic` and `--lane live-config` commands are
network-free compiler/configuration lanes. They do not create Docker
resources and do not consume the host admission budget. The rules below apply
to the host-side live execution coordinator (and to any local qualification
harness that exercises it).

## Invariants

1. Baseline and candidate work use one host-scoped budget. There is no separate
   baseline pool and no separate candidate pool.
2. Admission is all-or-nothing. The coordinator validates configuration,
   checks capacity, and obtains an atomic lease before it creates a Docker
   container, network, or exec stream. A waiting or rejected request creates
   none of those resources.
3. Capacity is checked twice: after a queue wait and immediately before
   workload creation. A stale or unavailable probe is a denial, not permission
   to proceed.
4. The coordinator exposes stable state and reason codes, not host names,
   interface names, Docker object IDs, memory totals, or other host detail.
5. Every lease has an expiry, heartbeat, and fencing token. Release is
   idempotent and occurs on success, failure, cancellation, expiry, and worker
   termination recovery.

A scorecard workload is the complete bounded execution owned by one baseline or
candidate request. If baseline and candidate are launched as two requests,
each requests the same shared budget and only the admitted request may start.
An orchestration layer may reserve one slot for a paired run, but it must not
silently multiply that reservation into independent per-mode capacity.

## Configuration and safe bounds

The coordinator rejects startup when a value is outside the range below, when
an integer is malformed, or when a relationship constraint is violated. The
values are host-side settings; they are not provider credentials and must not
be copied into evidence as secrets. Defaults are deliberately conservative for
the host control plane.

The two tables below are the complete admission and pressure surface: every
tuning input the coordinator reads is named here, and an unrecognized
`ARGUS_SCORECARD_*` variable is a startup error rather than a silent no-op.
Cross-setting constraints are part of the contract:

- `ARGUS_SCORECARD_BACKOFF_MAX_SECONDS` must be greater than or equal to
  `ARGUS_SCORECARD_BACKOFF_BASE_SECONDS`;
- `ARGUS_SCORECARD_HEARTBEAT_SECONDS` must be less than
  `ARGUS_SCORECARD_LEASE_SECONDS`, or a worker cannot renew before expiry;
- `ARGUS_SCORECARD_QUEUE_MAX_ITEMS=0` disables queueing, so a capacity denial
  fails the request instead of waiting; and
- `ARGUS_SCORECARD_MAX_ACTIVE_JOBS` is one value shared by baseline and
  candidate work, never a per-mode limit.

### Admission, queue, and lease settings

| Setting | Default | Valid range | Operational effect |
|---|---:|---:|---|
| `ARGUS_SCORECARD_ADMISSION_ENABLED` | `true` | `true` or `false` | `false` is the emergency stop for new scorecard work. It does not delete queued records, evidence, or unrelated Homelab state. |
| `ARGUS_SCORECARD_MAX_ACTIVE_JOBS` | `1` | integer `1..4` | One shared maximum for all baseline and candidate workloads on the host. The default prevents concurrent Docker bursts. |
| `ARGUS_SCORECARD_QUEUE_MAX_ITEMS` | `8` | integer `0..32` | Maximum waiting requests. `0` rejects capacity-denied work instead of queueing it. |
| `ARGUS_SCORECARD_QUEUE_MAX_WAIT_SECONDS` | `900` | integer `30..3600` | Maximum time a request may remain `waiting`; expiry becomes a terminal `failed` state with `queue_timeout`. |
| `ARGUS_SCORECARD_RETRY_LIMIT` | `6` | integer `0..10` | Maximum capacity/lease-recovery retries after the initial attempt. It prevents an unbounded queue resident. |
| `ARGUS_SCORECARD_BACKOFF_BASE_SECONDS` | `5` | integer `1..60` | Initial delay after a capacity denial or recoverable lease event. |
| `ARGUS_SCORECARD_BACKOFF_MAX_SECONDS` | `60` | integer `5..300` | Upper bound for exponential backoff. It must be greater than or equal to the base. |
| `ARGUS_SCORECARD_LEASE_SECONDS` | `900` | integer `60..3600` | Lifetime of an admission lease without renewal. Expiry makes the lease eligible for fenced recovery. |
| `ARGUS_SCORECARD_HEARTBEAT_SECONDS` | `15` | integer `5..60` | Worker heartbeat/renewal interval. It must be less than the lease lifetime. A missed heartbeat does not immediately delete the lease. |
| `ARGUS_SCORECARD_CANCEL_GRACE_SECONDS` | `10` | integer `1..60` | Time given to stop a canceled workload and release its lease before recovery marks it abandoned. |
| `ARGUS_SCORECARD_EVIDENCE_GUARD_SECONDS` | `5` | integer `5..30` | Minimum protected handoff around a runner-demand evidence check. Scorecard startup is denied while the check is active and waits for a fresh reservation. |

### Capacity and pressure settings

The probe samples host-wide state, not only the scorecard process. Counts include
existing non-scorecard Docker objects because the risk is pressure on the shared
Docker and network control plane.

| Setting | Default | Valid range | Operational effect |
|---|---:|---:|---|
| `ARGUS_SCORECARD_MIN_CPU_FREE_PERCENT` | `20` | integer `0..90` | Minimum host CPU headroom required at the final probe. A lower reading returns `capacity_cpu` and leaves the request queued. |
| `ARGUS_SCORECARD_MIN_MEMORY_FREE_MIB` | `1024` | integer `256..8192` | Minimum host available memory. A lower reading returns `capacity_memory`; swap is not counted as available memory. |
| `ARGUS_SCORECARD_MAX_DOCKER_CONTAINERS` | `64` | integer `1..256` | Maximum total Docker containers visible to the probe. At or above the limit, admission returns `capacity_containers`. |
| `ARGUS_SCORECARD_MAX_DOCKER_NETWORKS` | `16` | integer `1..64` | Maximum total Docker networks visible to the probe. At or above the limit, admission returns `capacity_networks`. |
| `ARGUS_SCORECARD_MAX_HOST_INTERFACES` | `256` | integer `16..1024` | Maximum host network interfaces, including veth devices, visible to the probe. At or above the limit, admission returns `capacity_interfaces`. |
| `ARGUS_SCORECARD_CAPACITY_PROBE_TIMEOUT_SECONDS` | `2` | integer `1..5` | Deadline for the CPU, memory, Docker, interface, and scheduler probes. A timeout is `capacity_unavailable`, never an admission. |
| `ARGUS_SCORECARD_CAPACITY_PROBE_MAX_AGE_SECONDS` | `5` | integer `1..30` | Maximum age of a cached probe. Older observations are unavailable and cause queueing until a fresh probe succeeds. |

The five-second evidence deadline is a Homelab runner-demand contract, not a
license to raise the scorecard probe timeout. Increasing a threshold to make a
run pass defeats the purpose of this guard and must be treated as a policy
change with a new qualification run.

## Admission sequence and shared budget

The coordinator performs this sequence for every baseline and candidate request:

1. Validate the immutable run identity, profile, corpus generation, and
   bounded deadline. Invalid requests fail before entering the queue.
2. If admission is disabled, return `policy_rejected` with `admission_disabled`.
3. Atomically create a content-free queue record if all shared slots are busy.
   The record contains a request reference, enqueue time, attempt count, and
   state; it does not contain host diagnostics.
4. For a waiting request, check queue age, cancellation, retry count, and the
   runner-demand evidence reservation.
5. Run a fresh capacity probe for CPU, memory, Docker containers, Docker
   networks, host interfaces/veth pressure, active scorecard leases, and probe
   availability.
6. Acquire a lease on the shared host budget atomically. The active-job check
   and lease acquisition are one fenced operation; two worker processes cannot
   both use the last slot.
7. Repeat the capacity probe and evidence reservation immediately before
   workload creation. If either check fails, release the lease and return to
   `waiting` with bounded backoff. No Docker call occurs on this path.
8. Mark the request `admitted`, then create the complete workload. Only after
   this point may the worker create containers, networks, or exec streams.
9. Mark it `running`, renew the lease while working, and persist terminal
   evidence before releasing the lease.

Baseline and candidate jobs therefore cannot independently multiply pressure:
a candidate cannot bypass a baseline lease, and a second baseline cannot bypass
a candidate lease. A request that is queued is not a partially started request.

## Queue, retry, and recovery states

An admission result is one of `admitted`, `waiting` (queued), or
`unavailable`; only `admitted` and `waiting` keep a durable record. The
operator status projection uses these states:

| State | Meaning | Next action |
|---|---|---|
| `waiting` | Valid work is queued because admission is unavailable or a shared slot is busy. | Observe `reason_code`, `attempt`, `next_retry_at`, and `expires_at`; do not treat it as workload failure. |
| `admitted` | A fenced host lease exists; Docker startup has not necessarily completed. | Watch for `running` or a bounded startup failure. |
| `running` | The workload owns the lease and may use Docker resources. | Check heartbeat, resource observations, and the worker receipt. |
| `canceled` | The caller canceled before terminal completion. | Verify lease release and owner-scoped cleanup. |
| `failed` | A terminal validation, startup, timeout, capacity-queue, or execution failure was recorded. | Use the stable reason code and retained evidence; do not retry outside the policy. |
| `completed` | The workload and evidence bundle finished and the lease was released. | Verify checksums and artifact receipt. |

Reason codes are intentionally content-free:
`capacity_active_jobs`, `capacity_cpu`, `capacity_memory`,
`capacity_containers`, `capacity_networks`, `capacity_interfaces`,
`capacity_unavailable`, `runner_demand_busy`, `queue_full`, `queue_timeout`,
`admission_disabled`, `canceled`, `lease_expired`, `worker_crashed`,
`startup_failed`, and `completed`.

Capacity denials use bounded exponential backoff:

```text
wait = min(ARGUS_SCORECARD_BACKOFF_MAX_SECONDS,
           ARGUS_SCORECARD_BACKOFF_BASE_SECONDS * 2**attempt)
```

The implementation may add jitter, but it must not exceed the configured
maximum or busy-loop. Queue timeout and retry-limit exhaustion are terminal;
transient capacity denial is not. A queue record with `state=waiting` and a
future `next_retry_at` is healthy backpressure, not a failed workload.

Cancellation removes a waiting request without touching other queue records. For
an admitted or running request, cancellation stops scheduling new work, waits
up to `ARGUS_SCORECARD_CANCEL_GRACE_SECONDS`, runs owner-scoped cleanup, and
releases the lease. A late worker cannot settle the request because the fencing
token has changed.

A worker crash or startup exception follows the same recovery path: mark the
lease suspect, wait for its expiry or prove the worker is gone, fence the old
owner, clean only resources labeled with that workload's owner, and release the
slot. If the request has not exceeded its queue deadline or retry limit, put it
back in `waiting` with `worker_crashed` or `lease_expired`; otherwise mark it
`failed`. Later queued work may proceed after the shared slot is released.
Never infer completion from an SSH/process exit alone; use the durable queue,
lease, and artifact receipts.

## Operator signals: queued versus failed

Use the coordinator status and the scorecard bundle together:

- **Queued:** `state=waiting`, a future `next_retry_at`, a non-expired
  `expires_at`, and no workload-start or Docker-resource receipt. The reason
  code is one of the capacity/runner-demand/queue codes.
- **Failed:** `state=failed`, a terminal `finished_at`, a terminal reason code,
  and a failure artifact or cleanup receipt. A queue timeout or retry-limit
  exhaustion is failed even though no Docker resource was created.
- **Running:** `state=running` with a current heartbeat and an unexpired lease.
- **Recovered:** a prior `lease_expired`/`worker_crashed` record has a cleanup
  receipt and a new waiting attempt, or a terminal failure if its bounds were
  exhausted.

Do not classify a canceled Docker API call, a missing worker process, or an
empty artifact directory by itself. Correlate request id, queue state, lease
state, reason code, and owner-scoped cleanup receipt.

## Homelab runner-demand coordination

Homelab runner-demand scheduling owns the five-second evidence checks used for
Docker, Hermes, and Homelab health. Scorecard work is a lower-priority consumer
of the same host and must never compete with those checks.

The coordinator must:

1. ask runner-demand for a fresh evidence reservation before each final
   capacity probe;
2. treat an active or unavailable reservation as `runner_demand_busy` and queue
   without creating Docker resources;
3. keep the reservation through the admission-to-start handoff, including the
   `ARGUS_SCORECARD_EVIDENCE_GUARD_SECONDS` guard;
4. yield before the next five-second evidence deadline and never hold a Docker
   exec stream across an evidence check; and
5. record the reservation result and each evidence-call latency in the
   qualification bundle.

The runner-demand scheduler remains authoritative for its own cadence and may
pre-empt or defer scorecard admission. Scorecard capacity thresholds do not
replace scheduler coordination, and a green scorecard result cannot waive a
failed or late five-second evidence check.

## Rollback and emergency disable

### Emergency stop without data deletion

1. Set `ARGUS_SCORECARD_ADMISSION_ENABLED=false` in the host coordinator's
   managed configuration and reload/restart only the coordinator. Do not use
   `docker system prune`, remove Docker networks globally, delete queue rows,
   or delete scorecard/Homelab evidence.
2. Confirm new submissions return `policy_rejected/admission_disabled` and that
   existing `running` work is visible with its lease and heartbeat.
3. Cancel active scorecard work through the coordinator if the host is under
   pressure. Allow owner-scoped cleanup and lease release to complete; do not
   kill unrelated containers or remove shared networks.
4. Leave waiting records and completed bundles intact. They are needed to
   explain the incident and to resume or intentionally cancel work later.
5. After runner-demand evidence is healthy, re-enable admission with the
   previous known-good configuration, then admit one disposable qualification
   workload before normal scheduling.

### Rollback after a coordinator or configuration change

Restore the previous immutable coordinator package and the last known-good
configuration as one change. Preserve the current queue, lease, and evidence
state; the restored coordinator must reconcile it by request id and fencing
token rather than resetting files or deleting rows. If a lease cannot be
proved safe, let it expire and perform owner-scoped cleanup before re-admitting
work. Rollback changes admission policy only; it must not roll back the Argus
PostgreSQL schema or delete unrelated Homelab data.

Record the disable/rollback reason, source/package identity, configuration hash,
active and waiting counts, cleanup receipts, and the first post-rollback
qualification result. A rollback receipt without those fields is not evidence
that the host is safe to resume.

## Bounded qualification

The qualification is disposable and must run outside production databases and
promotion. The harness is issue #157, delivered alongside the host-side live
execution coordinator; this repository's hermetic runner is not that harness.
It must be a bounded command with this invocation contract:

```bash
uv run python scripts/qualify-scorecard-admission.py \
  --baseline-image "$BASELINE_IMAGE" \
  --candidate-image "$CANDIDATE_IMAGE" \
  --duration 120s \
  --evidence-deadline 5s \
  --max-active-jobs 1 \
  --output .artifacts/scorecard-admission-qualification
```

Run the command from the live-execution checkout, not this repository. #157 may
change the script path, but the bounded flags above and the artifacts below are
the contract this document fixes; a change to either is a documentation change,
not an implementation detail.

Use disposable images and an isolated Docker project. Do not point the command
at the production database, production Docker project, promotion state, or
provider credentials. The qualification must start bounded baseline and
candidate activity through the real coordinator, exercise Docker, Hermes, and
Homelab runner-demand evidence calls concurrently, and interrupt cleanly once
the two-minute bound is reached.

Pass criteria:

- every evidence call completes within five seconds and none fails closed
  unexpectedly;
- the observed active scorecard count never exceeds
  `ARGUS_SCORECARD_MAX_ACTIVE_JOBS` (the default is one);
- the second workload is `waiting` until the first releases its lease;
- no waiting request creates a container, network, or exec stream;
- CPU, memory, Docker container/network, and interface pressure remain within
  their configured bounds before, during, and after the run;
- cancellation or interruption leaves zero scorecard containers, networks,
  leases, or queue entries; and
- the qualification exits successfully only with a completed cleanup receipt.

Required secret-free artifacts under the output directory:

```text
qualification.json          # bounds, config hash, start/end, final result
admission-events.jsonl      # waiting/admitted/running/retry/cancel/complete states
capacity-samples.jsonl      # before/during/after pressure observations
runner-demand-evidence.jsonl # each Docker/Hermes/Homelab call and latency
scorecard-receipts/         # baseline/candidate attempt and cleanup receipts
checksums.sha256
```

The bundle must report maximum evidence latency, active scorecard count, queue
transitions, lease expiry/recovery events, and Docker object/interface pressure
before, during, and after execution. It must contain no credentials, raw
provider payloads, host-specific secrets, or unrelated Homelab state. The
existing hermetic preflight remains useful but is not a qualification result:

```bash
uv run python scripts/run-scorecard.py \
  --lane hermetic \
  --output .artifacts/scorecard-hermetic
```

That command proves the offline scorecard contract only; it does not prove
admission, Docker pressure, or five-second runner-demand evidence.
