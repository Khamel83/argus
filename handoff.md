# Argus clean-session handoff

Last reviewed: 2026-09-07

## Current execution frontier

Release `1.6.4` is deployed and the production authority is healthy but
degraded. The historical readiness score is `54/100`; it is not a current
score. The current deployed source is `458db1a10e158aa9ec156e8eaa85d6fbed2fe3e3`
and the image is
`ghcr.io/khamel83/argus@sha256:1a7bba7a32ecd70f70e05e0fbc471ac58519c01c06c80ee30b688dce7b8eace4`.
The 1,800-second soak passed and the exact pair is both current and known-good.

The latest explicit free probes returned three results from SearXNG, Yahoo,
and GitHub. DuckDuckGo returned three after its cooldown, but also produced a
guarded acquisition-policy block and remains intermittent/fail-closed. Paid
providers are disabled as `not_registered` until truthful credential-version
and account-scope bindings exist; protected values are not proof of valid
current keys. No paid call occurred in this run, while the production ledger
retains 48 settled paid attempts from July.

One complete PEP 257 article extraction succeeded through Trafilatura and
created an acknowledged, release-bound Maya receipt. Browser capability is
still not admitted, and recovery remains degraded because the metadata
registry is incomplete. Python is not ambiguous: 3.11 is the package floor,
3.12.3 is the canonical repository/production runtime, and 3.13 is the
compatibility CI lane. Required CI run `34107922118` passed all three.

Use authenticated `/api/admin/status` and `/api/ready` for live identity and
readiness. Do not use the historical image/source pair or invent provider
account fingerprints from secret values. `/api/readiness` is not a route.

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
browser, recovery, or audit evidence:

1. Register truthful provider credential versions and account scopes, then run approved no-spend provider tests.
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
