<!-- repo-reconciliation:canonical-plan -->

# Argus repository plan

Single canonical ordered plan for outstanding Argus work. Produced by the
[repository reconciliation audit #192](https://github.com/Khamel83/argus/issues/192)
under tracking coordinator [#191](https://github.com/Khamel83/argus/issues/191).
[TODO.md](../TODO.md) remains the authoritative project task list; this plan
orders and classifies that list and the open native issues, it does not replace
their intents. Task prose stays in its owning record.

- Source base (fetched default branch): `cf20dd6eceba8aa67902166d1c044193104a3a5f`
- Intake packet: `docs/reconciliation/intake-2026-10-10.json` (+ parts 1–8)
- Dated coverage and classification evidence: [docs/history/2026-10-11-repository-audit-intake.md](history/2026-10-11-repository-audit-intake.md)
- Audit model makes no code/runtime change and enqueues no implementation.

## Captured input coverage

- Captured source revision: `cf20dd6eceba8aa67902166d1c044193104a3a5f`
  (matches this worktree HEAD `cf20dd6`).
- Manifest `docs/reconciliation/intake-2026-10-10.json`: `retained_records=171`,
  `open_records=16`, 8 record parts (29/19/17/26/24/22/22/12 records).
- All 8 part SHA-256 values were recomputed and match the manifest
  `record_parts` entries (see the dated history file for the table).
- Coverage string as captured: "Full open issue/PR bodies and open issue
  comments; closed body excerpts since 2026-07-12. Other review/deployment
  evidence may be missing."
- 20 records are explicitly `omitted` in the manifest: issues 5, 7, 9, 12, 13,
  15, 18, 19, 20 and PRs 1–4, 6, 8, 10, 11, 14, 16, 17 — all closed before the
  bounded history window. They are recorded as unreadable-by-design, not as
  evidence of completion.
- Closed-issue acceptance and all deployment/provider receipts outside the
  packet stay unknown here; a merged PR alone is not treated as runtime
  acceptance.

## Classification

States: **completed** (own criteria met with source evidence), **remaining**
(open, unblocked in principle), **blocked** (named gate: owner/access/deploy or
unmet dependency), **duplicate/superseded**, **unknown** (no captured evidence).

### Open native issues (16)

| Issue | State | Basis |
|---|---|---|
| [#191](https://github.com/Khamel83/argus/issues/191) coordinator | remaining (tracking) | `g2k:parked`; stays parked/tracking-only |
| [#192](https://github.com/Khamel83/argus/issues/192) audit | completed on merge | this plan; documentation-only |
| [#151](https://github.com/Khamel83/argus/issues/151) scorecard concurrency | remaining (parent) | decomposed into 154–158; parent tracks only |
| [#154](https://github.com/Khamel83/argus/issues/154) shared admission budget | blocked | needs-human; child of 151 |
| [#155](https://github.com/Khamel83/argus/issues/155) pressure gates | blocked | needs-human; depends on 154 |
| [#156](https://github.com/Khamel83/argus/issues/156) queue/recovery | blocked | needs-human; depends on 154 |
| [#157](https://github.com/Khamel83/argus/issues/157) qualification run | blocked | needs-human; depends on 154–156,158 |
| [#158](https://github.com/Khamel83/argus/issues/158) admission ops docs | blocked | needs-human; depends on 154–157 |
| [#177](https://github.com/Khamel83/argus/issues/177) mise adoption | blocked | blocked by Khamel83/infra#727 source acceptance |
| [#179](https://github.com/Khamel83/argus/issues/179) mise.toml | blocked | needs-human; child of 177 |
| [#180](https://github.com/Khamel83/argus/issues/180) uv.lock platforms | blocked | needs-human; child of 177 |
| [#181](https://github.com/Khamel83/argus/issues/181) mise tasks | blocked | needs-human; child of 177; premise mismatch (no Makefile) |
| [#182](https://github.com/Khamel83/argus/issues/182) CI via mise | blocked | needs-human; child of 177; premise mismatch (no lint/typecheck CI job) |
| [#183](https://github.com/Khamel83/argus/issues/183) mise docs | blocked | needs-human; child of 177 |
| [#148](https://github.com/Khamel83/argus/issues/148) paywall login browser | blocked (owner decision) | needs-human; scoped exception #149 merged, live NYT/WSJ acceptance unmet |
| [#159](https://github.com/Khamel83/argus/issues/159) AI chat share links | blocked | needs-human; depends on browser-policy cross-domain admission |

No open pull requests are present in the packet.

### TODO.md items

Completed (source evidence in the packet): H26 observer implementation/review
(PR #173), PR #166 spend-clock race repair, required CI on `b8642be`, `oci-ts`
retirement (PR #163), and the managed-pointer reconciliation (PR #189). The
eleven historical `[x]` restoration lines are completed. One line previously
marked open — exact-head review/CI for the H26 observer — is completed by the
merge of PR #173 and is corrected in TODO.md.

Remaining/blocked TODO items map to the ordered plan waves below.

### Closed records relevant to claimed completion

- PR #173 (observer) merged `2026-10-02`; its later guarded release and Homelab
  gates remain open (not runtime acceptance).
- PR #189 merged `2026-10-10`; pointer source eligibility only.
- PR #190 closed `2026-10-10` (docs-only reconcile), PR #187 merged
  `2026-10-07` (model-matched tick), PR #138 merged `2026-09-08`.
- Issue #185 closed `2026-10-06` with no captured completion evidence and
  overlaps #148 item 2; recorded **unknown/duplicate** (see dated history).
- Star-opportunity issues #139–#145 closed `2026-09-23` as parked/declined.

## Ordered plan

Waves are ordered by dependency. Each item lists its acceptance, owning record
and gate. "Runtime acceptance: pending" items must not be claimed done from
source alone.

### Wave 0 — this audit (merge-gated, documentation only)

- Deliverables: this plan, the dated coverage history, and TODO/CONTEXT/HANDOFF
  corrections. Acceptance: trusted exact-head review and normal merge; coverage
  and blockers reported. Owning record: #192.

### Wave 1 — scorecard admission coordinator (blocked)

1. #154 shared host-wide admission budget (default one active workload, atomic
   acquire/release, crash recovery). Blocks 155–158.
2. #155 bounded host/Docker pressure gates (CPU, memory, Docker object/interface
   pressure, active jobs; fail closed to queue).
3. #156 bounded queue/backoff/cancel/lease recovery; second job waits for first.
4. #157 bounded qualification run proving Baywatch evidence calls stay within
   their five-second budget; no production DB/deploy.
5. #158 operations/coordination documentation incl. rollback/emergency disable.

Owner decision: remove `needs-human` on each child and authorize the finite
chain before any job runs. Acceptance for each is in its own issue body. No
Docker resource may be created before admission.

### Wave 2 — mise dev-environment adoption (blocked)

1. Khamel83/infra#727 source acceptance must complete first (external gate).
2. #179 `mise.toml` runtime declaration (leave pyproject/uv.lock unchanged).
3. #180 multi-platform `uv.lock` metadata for linux-x86_64, linux-aarch64,
   macos-aarch64; documented command and platform list.
4. #181 mise tasks wrapping existing dev workflows.
5. #182 CI migration to `mise run`, preserving cache keys/artifacts.
6. #183 mise adoption documentation incl. rollback.

Owner decision / correction: #181 and #182 assume an existing `Makefile` and
existing `lint`/`typecheck` CI jobs. This repository has no `Makefile` and CI
has no lint/typecheck job (`.github/workflows/ci.yml` uses `uv` directly with
`test`, `scorecard`, `postgres-ledger`, `production-config`). Their premises
must be corrected before admission. The prior environment is the rollback.

### Wave 3 — paywall/browser capability (blocked, owner decision)

- #148: the scoped off-by-default exception (`ARGUS_AUTH_BROWSER_DOMAINS`, PR
  #149) is merged but live NYT+WSJ extraction is unmet; the long-term
  browser-network attestation is absent. Acceptance remains: with the switch on
  one NYT and one WSJ article extract from Homelab, with it off both fail closed;
  tests cover off-by-default, allowlist, same-origin, and receipt marking; item 2
  tracked to completion then the exception retired. Browser access stays
  fail-closed. Owner decision required for an external network authority.
- #159: AI chat share links need browser-policy admission for the
  `share.gemini.google → gemini.google.com` redirect and canary tests for a
  Gemini, ChatGPT, and Claude public share. Depends on the same browser path.

### Wave 4 — provider reconciliation (blocked, owner authorization)

- Fresh authorized validation for providers whose one-call canary failed before
  the transport repair; failures and uncertain reservations preserved.
- Reconcile uncertain provider charges only with authoritative provider
  evidence; never convert an HTTP rejection into assumed zero spend.
- No billing changes, quota resets, credential rotation, or repeated provider
  tests are authorized by the audit or this plan.

### Wave 5 — bounded source debt (remaining, not yet ready)

- Automate the exact digest scorecard-admission handoff; retain bounded residual
  semantics when the evaluator is absent (depends on Wave 1).
- Close remaining pool/lifespan, authenticated-browser shutdown, and workflow
  shutdown/finalization debt (browser-shutdown portion couples to Wave 3).
- Review the optional workflow LLM gateway's admission and error boundary before
  enabling it (target module not located in this repository).
- Add deliberate type/format governance separately from restoration; do not
  reformat unrelated user work (design decision still open).

### Wave 6 — H26 publication and observation gates (blocked, deploy/owner)

- Use guarded digest/source release and explicit nonsecret opt-in; accept a
  natural redacted metadata receipt. Production remains `e8f36cf`.
- Homelab owns protected source binding, credential-free reader acceptance,
  durable health-log admission, and notification evidence; readiness unknown.
- Confirm the next natural Janitor OCI observation of source `e8f36cf` has an
  archived receipt (homelab#296 holds its other fleet gates).
- Verify the next natural publisher receipt (PR #189 merged; publisher not yet
  observed).
- Reconcile the inherited extraction-completion conflict (PR #138 model-matched
  vs archived generated unchecked) — owner decision; keep unresolved until
  owning source evidence resolves it.

Runtime acceptance: pending for every Wave 6 item; none may be closed from
source evidence alone.

## Owner decisions required

1. Whether to remove `needs-human` and admit Wave 1 (scorecard admission).
2. Correct the `Makefile`/lint-job premises in #181/#182 before Wave 2.
3. External browser-network attestation authority for #148/#159.
4. Provider re-validation and charge reconciliation authorization (Wave 4).
5. Resolution of the extraction-completion conflict and the guarded H26 release.

## Proposed native dispositions

- #192: close on merge (audit complete).
- #191: keep open, `g2k:parked` (tracking-only).
- #151: keep open as parent; close only when Wave 1 children complete.
- #154–#158, #177–#183, #148, #159: keep open; blocked as classified above.
- #185, #139–#145: no change (already closed).
- No issue is closed, re-labeled, or enqueued by this audit.

<!-- g2k:ready-jobs:v1 -->
```json
{"jobs": []}
```

Empty batch: no independently ready job exists in the captured inputs. Every
candidate is covered by an owner/deploy gate, an unmet dependency, an
unresolved premise, or already has a native issue. Blockers:

- Wave 1 (#154–#158): `needs-human` gate plus a sequential internal chain; the
  parent #151 is tracking-only.
- Wave 2 (#177–#183): blocked by Khamel83/infra#727; #181/#182 premises
  (Makefile, lint/typecheck CI jobs) do not exist in this repository.
- Wave 3 (#148, #159): owner decision for an external browser-network
  attestation; browser stays fail-closed.
- Wave 4: owner authorization for provider calls/charge reconciliation.
- Wave 5: under-specified or depends on unimplemented Wave 1; the LLM-gateway
  review target is not located in this repository.
- Wave 6: operational deployment/observation gates; Runtime acceptance pending.
