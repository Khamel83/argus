# Execution result — September 7, 2026

The requested restoration deployment has completed its required soak. See
[STATUS](docs/STATUS.md) for the exact deployed image, all provider outcomes,
MCP/extraction/Maya evidence and limitations, and [TODO](TODO.md) for remaining
work. Source fixes are merged in PRs 135 and 136; all required CI passed.
Do not repeat the completed provider canaries. Serper returned 403 and Valyu 402;
no provider retry or billing change is authorized by this checkpoint.

The old handoff below is historical task context, not current runtime state.
The final soak passed; the corrected image is current and known-good.
API/MCP are healthy and Valyu is disabled. The remaining capability limits
are documented rather than retried.

---

# Argus clean-session handoff

Last reviewed: 2026-09-07

## Historical pre-execution frontier

Release `1.6.4` is deployed and the production authority is healthy but
degraded. The historical readiness score is `54/100`; it is not a current
score. The current deployed source is `458db1a10e158aa9ec156e8eaa85d6fbed2fe3e3`
and the image is
`ghcr.io/khamel83/argus@sha256:1a7bba7a32ecd70f70e05e0fbc471ac58519c01c06c80ee30b688dce7b8eace4`.
The 1,800-second soak passed and the exact pair is both current and known-good.

Stored free-probe records are mixed by timestamp. Earlier evidence recorded
three results from SearXNG, Yahoo, and GitHub; the latest user-reported checks
recorded three DuckDuckGo results and three GitHub results, while sampled
SearXNG searches were empty and Yahoo returned HTTP 502. DuckDuckGo also
produced a guarded acquisition-policy block and remains intermittent/fail-
closed. Paid providers were disabled as `not_registered` before the explicit
live-validation authorization below; no paid call had occurred at that point.
The production ledger retains 48 settled paid attempts from July.

One complete PEP 257 article extraction succeeded through Trafilatura and
created an acknowledged, release-bound Maya receipt. Browser capability is
still not admitted, and recovery remains degraded because the metadata
registry is incomplete. Python is not ambiguous: 3.11 is the package floor,
3.12.3 is the canonical repository/production runtime, and 3.13 is the
compatibility CI lane. Required CI run `34107922118` passed all three.

Use authenticated `/api/admin/status` and `/api/ready` for live identity and
readiness. Do not use the historical image/source pair or invent provider
account fingerprints from secret values. `/api/readiness` is not a route.

## 2026-09-07 full-provider-coverage continuation

This section is the authoritative handoff for the next execution session. It
supersedes the earlier instruction that no credentialed provider call may be
made. The earlier no-call result remains valid historical evidence: no paid
provider call had occurred before this authorization.

### User authorization and objective

The user has explicitly confirmed the supplied provider credentials and
authorized bounded live validation. A provider test may consume a small amount
of an available quota or balance; it must not drain, loop, paginate, or retry
until exhausted. The objective is to establish full coverage for every
configured provider that the accounts and network can actually support, repair
any remaining implementation or deployment defects, update the audit, and
redeploy only after the evidence is complete.

Do not ask the user for the keys again. Do not print, echo, log, commit, or
place any raw credential in an evidence file, prompt, issue, receipt, or public
repository. The exact values are already in the private encrypted Homelab
vault. Read only secret-safe names and status. Account scope must come from
truthful provider/account evidence, never from a hash of a secret.

The prior diagnosis that the keys were simply missing or invalid was wrong.
Before the refresh, the key names already existed in the encrypted services
vault and the running production environment already contained non-empty
provider variables. The real blockers were missing readiness registrations,
an incomplete vault projection, and a Wolfram environment-variable mismatch.
The projection and Wolfram mapping were repaired and redeployed; provider
registration and live provider-effect evidence are still outstanding.

### Fresh live control-plane evidence

The latest read-only check was run against the live `argus` container on
2026-09-07. It printed no secrets:

```text
/api/ready  200  status=degraded ready=true
  reason_codes=provider:duckduckgo, provider:github, provider:searxng,
               provider:yahoo, maya, browser, recovery
/api/health 200  status=ok version=1.6.4 semantics=liveness_compatibility
/api/live   200  status=alive
/api/admin/status 200  ready=true status=degraded
```

The current admin provider states are:

| Provider | Current state |
|---|---|
| Brave | `disabled` |
| DuckDuckGo | `degraded` |
| Exa | `disabled` |
| GitHub | `degraded` |
| Linkup | `disabled` |
| Parallel | `disabled` |
| SearchAPI | `disabled` |
| SearXNG | `degraded` |
| Serper | `disabled` |
| Tavily | `disabled` |
| Valyu | `disabled` |
| WolframAlpha | `disabled` |
| Yahoo | `degraded` |
| You.com | `disabled` |

`disabled` does not prove an invalid key. For the credentialed providers it
currently means the readiness gate has no truthful credential-version and
account-scope registration, or the provider is deliberately disabled by
policy. No provider should be called until its registration, enablement, and
bounded-test record are reconciled.

### Current secret projection, without values

The running container reported non-empty values for these environment names:

```text
ARGUS_BRAVE_API_KEY
ARGUS_TAVILY_API_KEY
ARGUS_EXA_API_KEY
ARGUS_LINKUP_API_KEY
ARGUS_PARALLEL_API_KEY
ARGUS_SERPER_API_KEY
ARGUS_YOU_API_KEY
ARGUS_VALYU_API_KEY
ARGUS_WOLFRAM_API_KEY
```

Current enable flags in the running container were:

| Provider | Enable flag |
|---|---:|
| Brave | `true` |
| Tavily | `true` |
| Exa | `false` |
| Linkup | `true` |
| Parallel | `true` |
| Serper | `true` |
| You.com | `true` |
| Valyu | `false` |
| WolframAlpha | `false` |

The private encrypted services vault contains the nine supplied credential
names, with Wolfram stored as `WOLFRAM_APP_ID`. The projection now maps that
value to `ARGUS_WOLFRAM_API_KEY`, which is the name consumed by Argus. The old
`ARGUS_WOLFRAM_APP_ID` Compose mapping was incorrect.

SearchAPI has no supplied key and is therefore not testable. It must remain
`unconfigured` unless a key is already present in the private vault; do not
invent one and do not ask the user to resend credentials.

### Provider facts and safe validation scope

The supplied provider set is Brave, Tavily, Exa, Linkup, Parallel, Serper,
You.com, Valyu, and WolframAlpha. The keyless/free set is SearXNG,
DuckDuckGo, Yahoo, and GitHub. Full coverage means one truthful current
result for every provider that has a configured credential or free path, with
an explicit blocked/unconfigured result where account or network policy makes
that impossible.

The previously researched authentication contracts are:

| Provider | Authentication contract | Official reference |
|---|---|---|
| Brave | `X-Subscription-Token` | <https://api-dashboard.search.brave.com/documentation/guides/authentication> |
| Tavily | `Authorization: Bearer ...` | <https://tavilyai.mintlify.app/documentation/api-reference/introduction> |
| Linkup | `Authorization: Bearer ...` | <https://docs.linkup.so/pages/documentation/platform/authentication> |
| Parallel | `x-api-key` | <https://parallel.ai/products/search> |
| You.com | `X-API-Key` | <https://you.com/docs/api-reference/search/v1-search> |
| Valyu | `X-API-Key` | <https://docs.valyu.ai/api-reference/endpoint/search> |
| WolframAlpha | AppID/`appid` or documented bearer form | <https://products.wolframalpha.com/llm-api/documentation> |
| Serper | Verify against its current official API contract before testing | Do not rely on an inferred header |

Use the canonical Argus provider adapters. Do not bypass the broker with an
ad-hoc request that would omit provider traces, budget accounting, egress,
machine, or durable evidence.

Private operator facts that affect policy, without reproducing credentials:

- Linkup balance was recorded as `$39.98`.
- You.com available credits were recorded as `$192.06`.
- Valyu was recorded as `$0.13` in arrears and was previously kept disabled.
  A single bounded validation is allowed only if the provider accepts it
  without a billing mutation; otherwise record `blocked_by_account` and do
  not retry.
- Parallel must use the approved primary account scope. The alternate account
  recorded as burned must never be used.
- The WolframAlpha application named `Argus` last worked on 2026-07-29.
- SearchAPI has no supplied key.

### Python contract

There are three Python interpreters on the development Mac:

```text
/Users/macmini/.local/bin/python3.11  -> Python 3.11.15
/Users/macmini/.local/bin/python3.12  -> Python 3.12.13
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13
                                      -> Python 3.13.2
```

This is a local compatibility/toolchain inventory, not a three-Python
production runtime. The homelab host and both production containers run
Python 3.12.3. Argus declares Python 3.11 as its package floor, uses 3.12 as
the production baseline, and tests 3.13 as a compatibility lane. CI matrix
run `34107922118` passed all three lanes. The old HTTP 500 was caused by an
overlong readiness lease-owner value and is fixed; it was not a Python-version
failure.

Do not change the package floor or remove the CI lanes merely to hide the
local installations. Standardize local development and production on Python
3.12; leave 3.11 and 3.13 as compatibility-test lanes.

### Deployment and repository state

- Production package: `1.6.4`.
- Deployed source revision:
  `458db1a10e158aa9ec156e8eaa85d6fbed2fe3e3`.
- Deployed image:
  `ghcr.io/khamel83/argus@sha256:1a7bba7a32ecd70f70e05e0fbc471ac58519c01c06c80ee30b688dce7b8eace4`.
- Both `argus` and `argus-mcp` are healthy with zero restarts.
- PostgreSQL schema head is `0011_extraction_spend_scope`.
- The runtime database role cannot create schema objects.
- The review checkout is
  `/Volumes/2TB_SSD/GitHub/argus/.worktrees/argus-readiness-20260906`.
- The review checkout was clean before this handoff edit, on
  `codex/argus-readiness-20260906`, with local documentation commits ahead of
  its remote branch.
- The private Homelab projection refresh was committed locally as
  `d0dcaf1` in the Mac checkout and `87301e2` in the homelab checkout. These
  commits were not pushed to a GitHub remote by the prior session.
- The homelab production checkout contains a pre-existing user-owned change
  to `secrets/maya.env.encrypted`; preserve it and do not reset or overwrite
  it.
- The primary Argus checkout also contains user-owned dirty audit files under
  `.audit/` and an untracked root `AUDIT_REPORT.md`; inspect them before any
  merge or copy and do not discard them.
- Deploy through the root Homelab Compose file. Using the Argus service
  fragment directly caused a container-name conflict because the live
  containers are owned by the root Compose include graph.
- Never push encrypted or plaintext credentials to the public Argus remote.

### Required execution plan

Execute in this order. Every item needs evidence, not only a code diff.

1. Read `AGENTS.md`, this handoff, `CONTEXT.md`, the current audit report,
   `SYSTEM_TOPOLOGY.md`, and `HEALTH_CHECK_RUNBOOK.md`. Refresh GitHub,
   homelab, source, image, deployed runtime, and database identity before
   changing anything.
2. Reconcile the full path
   `encrypted vault -> generated host environment -> Compose -> running
   container -> Argus config -> readiness registry`. Print names, booleans,
   fingerprints, and states only; never values.
3. Confirm the provider registration schema and record truthful
   credential-version fingerprints, account scopes, finite budgets, and
   enablement for every supplied credential. Obtain account scope from the
   provider dashboard/API or existing private evidence. Never derive scope
   from a secret hash and never fabricate a registration to make a test pass.
4. Add or repair regression tests for every projection and naming mapping,
   especially the canonical `services` vault section, the legacy fallback,
   Brave/Parallel/Valyu mappings, and `WOLFRAM_APP_ID` to
   `ARGUS_WOLFRAM_API_KEY`.
5. Run one bounded authenticated canary per supplied provider. Use a unique
   release-bound idempotency key, `max_results=1` where the provider supports
   it, no pagination, no loop, and at most one narrowly justified retry. Use
   the real Argus authority path so the trace, spend reservation/settlement,
   normalized result, egress, machine, and durable receipt are recorded.
   Wolfram should use a deterministic low-cost computation if its adapter
   supports that mode. For Valyu, stop on the arrears response and record the
   blocked account outcome; do not mutate billing.
6. Run fresh free-provider probes for SearXNG, DuckDuckGo, Yahoo, and GitHub,
   separately recording HTTP outcome, result count, upstream/error category,
   egress, and cooldown behavior. The existing records conflict by timestamp:
   prior docs say SearXNG/Yahoo/GitHub returned three, while the latest user
   report says SearXNG sampled searches were empty and Yahoo returned 502.
   Treat this as unresolved until a new matrix run is captured.
7. Repair SearXNG upstream engines and Yahoo egress only where the evidence
   identifies a safe, reversible infrastructure fix. Do not weaken fail-closed
   acquisition policy or mislabel CAPTCHA/access-denied responses as success.
8. Admit the browser runtime only with the required external browser-network
   attestation, then prove one target-page extraction through the admitted
   path. A prior complete PEP 257/Trafilatura result is evidence for that
   page/path only; it does not prove browser or universal extraction coverage.
9. Run one reviewed Maya operation outside `excludes_capture`, record the
   current durable receipt, and separate receipt durability from the short-TTL
   current observation. Complete the recovery metadata-registry gate.
10. Re-run the complete audit and issue a replacement readiness score. Keep
    the original `54/100` explicitly historical and show the scoring delta.
    Update the relevant TODO/checklist in this handoff, `CONTEXT.md`,
    `docs/STATUS.md`, `docs/operations.md`, `docs/operations-status.md`,
    `docs/providers.md`, and the private audit/evidence records. There is no
    tracked `TODO.md` in this checkout; do not invent one unless the refreshed
    repository structure requires it.
11. Commit source, private deployment, and documentation changes separately
    where practical. Run the required CI and audit checks. Redeploy through
    the root Homelab Compose project, verify source/image/runtime identity,
    then verify authenticated HTTP, MCP, provider effects, durable writes,
    browser, recovery, and Maya receipts after deployment.
12. Finish with a provider-by-provider table containing these columns:
    configured, registered, enabled, attempted, HTTP outcome, normalized
    result count, spend/balance effect, durable trace/receipt, and consumer
    effect. Do not say “working” when only a key is present or an adapter test
    passed.

### Acceptance definition for this handoff

The next session is complete only when every supplied provider is either:

- `working`: current authenticated call succeeded, result/answer normalized,
  provider effect recorded, and durable evidence exists; or
- `blocked_by_account`, `blocked_by_network`, `unsupported`, or
  `unconfigured`: the reason is current, specific, reproducible, and
  documented, with no false readiness claim.

“All keys are present,” “the adapter exists,” “HTTP 200,” and “ready=true” are
not provider coverage. The final audit must report those layers separately.

### Copy/paste prompt for the frontier Astra reviewer

```text
You are the frontier top-tier Astra reviewer and implementation agent for
Argus. Execute the attached handoff; do not merely summarize it. The user has
explicitly authorized bounded live validation of every supplied provider key.
Do not ask for credentials again. The values are already in the private
encrypted Homelab vault. Never print, echo, log, commit, or include raw
secrets in your output or evidence.

Start by reading AGENTS.md, handoff.md, CONTEXT.md, the current .audit audit
report, SYSTEM_TOPOLOGY.md, and HEALTH_CHECK_RUNBOOK.md. Inspect current
source, GitHub, private Homelab checkout, encrypted vault projection, image,
containers, PostgreSQL schema, and authenticated /api/admin/status. Preserve
all user-owned dirty files. Treat the handoff as a snapshot and refresh every
external identity before acting.

Resolve full provider coverage end to end. Reconcile encrypted vault -> host
environment -> Compose -> running container -> Argus config -> readiness.
Record truthful credential-version fingerprints, account scopes, budgets, and
enablement using existing Argus conventions. Never invent account scope or
derive it from a secret hash. The prior missing-registration diagnosis,
incomplete services-vault projection, and Wolfram variable mismatch are known
issues; verify the fixes rather than assuming them.

For each configured provider, run exactly one bounded authenticated canary
through the production Argus authority path. Use max_results=1 where valid,
one unique release-bound idempotency key, no pagination, no loops, and at most
one narrowly justified retry. This user authorization permits a small quota
or balance charge but not depletion. Record sanitized HTTP outcome, provider
trace, normalized result count, egress, machine, spend/balance effect, durable
receipt, and downstream consumer effect. Test Brave, Tavily, Exa, Linkup,
Parallel, Serper, You.com, WolframAlpha, and Valyu if the account accepts a
single safe request. If Valyu rejects because of the recorded arrears, stop
and record blocked_by_account without changing billing or retrying. SearchAPI
has no supplied key and must remain unconfigured.

Run fresh separate probes for SearXNG, DuckDuckGo, Yahoo, and GitHub. Resolve
the known SearXNG upstream and Yahoo egress failures without weakening
fail-closed behavior. Do not convert an HTTP response with zero results,
CAPTCHA, access denial, suspension, or a guarded cooldown into success.

Continue through the remaining readiness gates: external browser-network
attestation, complete target-page extraction, reviewed Maya operation outside
excludes_capture with a current durable receipt, recovery metadata-registry
evidence, and a fresh audit score. Keep the original 54/100 historical. Do
not claim full readiness until each gate has current evidence.

Standardize production and local default execution on Python 3.12. The
package floor is 3.11 and CI must continue testing 3.11, 3.12, and 3.13.
Do not misdiagnose the old readiness-lease-owner HTTP 500 as a Python-version
problem.

Update the audit and operational documentation with exact dates, source/image
identity, current provider matrix, raw failure categories, topology, open
gates, and evidence links. Update handoff.md, CONTEXT.md, docs/STATUS.md,
docs/operations.md, docs/operations-status.md, docs/providers.md, and the
private evidence repository as applicable. Keep secrets out of public Git.
Run tests and the prescribed audit checks before any completion claim. Commit
changes with clear boundaries and redeploy only through the root Homelab
Compose project. Verify post-deploy authenticated HTTP, MCP, provider traces,
durable PostgreSQL evidence, browser, recovery, and Maya receipt.

End with a table for every provider showing: configured, registered, enabled,
attempted, HTTP outcome, normalized result count, spend/balance effect,
durable trace/receipt, and consumer effect. Use only working,
blocked_by_account, blocked_by_network, unsupported, or unconfigured when
evidence supports it. “Key present,” “adapter exists,” “HTTP 200,” and
“ready=true” are not proof of provider coverage.
```

## Start here

Read the last commit and this file:

```bash
git log -1 --oneline
git show --stat HEAD
sed -n '1,240p' handoff.md
```

Canonical agent guidance remains in `AGENTS.md`. The GitHub issues linked below own detailed acceptance criteria; this file only records the current execution frontier.

## Completed state

- The broad reliability implementation through issue #39 is merged into `main`.
- The truthful operational-status implementation landed in `1956910` after all seven exact-head CI jobs passed, including PostgreSQL and production-image canaries.
- HTTP is the sole production execution authority; MCP is a stateless authenticated HTTP adapter.
- User-visible retrieval history belongs in Maya. Argus owns useful bounded internal operational evidence.
- Production runs on the homelab in containers. The Mac mini is development-only and must not run Docker or Compose for Argus.
- OCI and Clio are retired from the intended architecture. Private Tailscale ingress remains the target.
- A manual deployment trigger exists in `.github/workflows/docker-publish.yml`; `docs/releasing.md` documents why `[skip ci]` must be reserved for documentation-only commits.
- The current readiness correction is deployed: accepted extraction outcomes
  atomically bridge to the Maya outbox, and provider-readiness lease owners are
  bounded to the database column limit.
- No open pull requests existed when this handoff was written.

## Outstanding capability gates

These are the active TODOs for full production readiness. The implementation
and deployment work is complete, but these gates require external provider,
browser, recovery, or audit evidence. Item 1 is now explicitly authorized by
the user for bounded live calls; the older no-spend wording is retained only
as historical policy context:

1. Register truthful provider credential versions and account scopes, then run bounded live provider tests without draining balances.
2. Stabilize DuckDuckGo guarded egress/cooldown behavior and reduce SearXNG upstream failures.
3. Admit an external browser-network attestation and record one current browser observation.
4. Complete recovery metadata-registry evidence.
5. Re-run the audit and issue a replacement readiness score.

The image workflow still fails closed when the exact scorecard admission file
is absent. Automating that admission handoff is an operational follow-up; the
current release was safely admitted and promoted manually.

## Outstanding GitHub issues

Work them in this order:

1. [#40 — Run Argus on shared homelab PostgreSQL with verified recovery](https://github.com/Khamel83/argus/issues/40)
   - Keystone issue; its code toolkit is already merged in `8f5e2e1`.
   - Owner decision recorded on the issue: existing SQLite history is disposable, so PostgreSQL starts fresh rather than importing history.
   - Remaining work is production operations: isolated Argus database/roles, private network reachability, durable encrypted configuration, backup scheduling, 7/5/12 retention, disposable restore proof, recovery evidence, and rollback verification.
   - Do not run the broad shared-cluster provisioning script blindly against the live Atlas database; the issue comment documents the ownership mismatch and calls for surgical Argus provisioning.

2. [#41 — Promote immutable homelab releases with rollback proof](https://github.com/Khamel83/argus/issues/41)
   - Manual deployment exists, but immutable digest promotion, provenance/SBOM, pinned actions, serialized promotion, candidate gates, and proven rollback remain.

3. [#42 — Cut over private homelab production and retire duplicate authorities](https://github.com/Khamel83/argus/issues/42)
   - MCP-to-HTTP production forwarding was verified previously.
   - The corresponding live homelab Compose adjustment still needs to be captured in the homelab infrastructure repository.
   - Finish the PostgreSQL authority transition, private ingress, client canaries, secret rotation, and duplicate-authority retirement only after #40 and #41.

4. [#44 — Publish the Argus production operations and recovery runbook](https://github.com/Khamel83/argus/issues/44)
   - Final documentation issue after #40–#42 establish the real end state.

## Safety and execution boundaries

- Inspect current GitHub and homelab state before acting; this handoff is a snapshot, not proof that production has not drifted.
- Never expose credentials, provider keys, database passwords, tailnet credentials, or decrypted secret-store contents.
- Use native tooling on the Mac. Container tests belong in GitHub Actions or explicitly authorized disposable homelab canaries.
- Production PostgreSQL, persistent volumes, credentials, network exposure, deployment, and cutover require explicit authorization and reversible checkpoints.
- Preserve Atlas tenant isolation. Verify backups and restores rather than treating a running database as recovery proof.
- Keep operational evidence bounded and useful; do not ship raw logs into Maya.

## Suggested skills for the next session

- `github:github` to refresh issue and pull-request state.
- `executing-plans` or `subagent-driven-development` only after selecting an authorized issue.
- `systematic-debugging` for any failed production or CI gate.
- `verification-before-completion` before closing an issue or claiming production reliability.
- `handoff` again before clearing the next long session.
