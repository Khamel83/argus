# Argus clean-session handoff

Last reviewed: 2026-07-25

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
- No open pull requests existed when this handoff was written.

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
