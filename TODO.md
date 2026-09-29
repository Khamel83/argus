# Argus operational follow-up

- [ ] Complete the Janitor #296 runtime identity receipt. PR #162's admitted
  image passed candidate gates but failed the pre-cutover Atlas restore schema
  inventory. The cause and narrow fix are in
  `docs/evidence/2026-09-29-restore-inventory-debug.md`. Merge the fix,
  make a fresh backup with its fingerprint, prove disposable restore, admit
  the exact selected image, promote, verify live `/api/ready`, then obtain a
  natural Janitor OCI observation with a durable receipt. Production remains
  at the prior image until the gate passes.
- [x] Retire the old `oci-ts` AI Review workflow. PR #163 merged as `bdca7fb`.
  Keep public/fork CI on GitHub-hosted runners. Its separate image failed
  exact scorecard admission; issue triage from the old workflow has no
  replacement proven here.

Updated September 7, 2026. The current dated capability evidence belongs in
[public status](docs/STATUS.md); the private audit preserves raw receipts.
A checked source task does not imply every provider has passed a live call.

- [x] Bound authenticated admin provider canaries to one uncached result and one consumed authorization, with no fallback.
- [x] Restore canonical vault projection, Wolfram naming, explicit account registration and retained scoped caller/Maya configuration.
- [x] Repair native HTTP POST framing and test actual socket bytes.
- [x] Bind new paid/free probe spend records to the validated baked release identity.
- [x] Repair DDG's framing ownership and credential-free redirect policy without weakening public-address checks.
- [x] Verify disposable PostgreSQL migrations, all production metadata, no drift and canonical restore.
- [x] Repair temporary scorecard network allocation, evidence schema and current accepted-operation promotion accounting.
- [x] Complete authenticated MCP, extraction and one-attempt Maya capture checks on the corrected image.
- [x] Finish the automatic corrected-image promotion soak and restore Valyu disablement.
- [ ] Correct extraction release configuration/binding and preserve full source URL; existing receipts remain unchanged.
- [ ] Admit a real external browser-network authority/attestation and prove a browser-assisted extraction. Keep browser access fail-closed meanwhile.
- [ ] Obtain fresh authorized validation for providers whose one-call canary failed before the transport repair; preserve those failures and uncertain reservations.
- [ ] Reconcile uncertain provider charges only with authoritative provider evidence. Never convert an HTTP rejection into assumed zero spend.
- [ ] Automate the exact digest scorecard-admission handoff; retain bounded residual semantics when the evaluator is absent.
- [ ] Close remaining pool/lifespan, authenticated-browser shutdown and workflow shutdown/finalization debt.
- [ ] Review the optional workflow LLM gateway's admission and error boundary before enabling it as a production capability.
- [ ] Add deliberate type/format governance separately from restoration; do not reformat unrelated user work.

SearchAPI has no configured key. Valyu must remain account-blocked if it rejects
the bounded request; this checklist does not authorize billing changes, quota
resets, credential rotation or repeated provider tests.
<!-- janitor:begin:todo -->
## Open follow-ups

_Updated from remote TODO.md evidence (base: 2026-09-07). A checked item does not imply every provider has passed a live call._

- [x] Bound authenticated admin provider canaries to one uncached result and one consumed authorization, with no fallback.
- [x] Restore canonical vault projection, Wolfram naming, explicit account registration and retained scoped caller/Maya configuration.
- [x] Repair native HTTP POST framing and test actual socket bytes.
- [x] Bind new paid/free probe spend records to the validated baked release identity.
- [x] Repair DDG's framing ownership and credential-free redirect policy without weakening public-address checks.
- [x] Verify disposable PostgreSQL migrations, all production metadata, no drift and canonical restore.
- [x] Repair temporary scorecard network allocation, evidence schema and current accepted-operation promotion accounting.
- [x] Complete authenticated MCP, extraction and one-attempt Maya capture checks on the corrected image.
- [x] Finish the automatic corrected-image promotion soak and restore Valyu disablement.
- [ ] Correct extraction release configuration/binding and preserve full source URL; existing receipts remain unchanged.
- [ ] Admit a real external browser-network authority/attestation and prove a browser-assisted extraction. Keep browser access fail-closed meanwhile.
- [ ] Obtain fresh authorized validation for providers whose one-call canary failed before the transport repair; preserve those failures and uncertain reservations.
- [ ] Reconcile uncertain provider charges only with authoritative provider evidence. Never convert an HTTP rejection into assumed zero spend.
- [ ] Automate the exact digest scorecard-admission handoff; retain bounded residual semantics when the evaluator is absent.
- [ ] Close remaining pool/lifespan, authenticated-browser shutdown and workflow shutdown/finalization debt.
- [ ] Review the optional workflow LLM gateway's admission and error boundary before enabling it as a production capability.
- [ ] Add deliberate type/format governance separately from restoration; do not reformat unrelated user work.

SearchAPI has no configured key. Valyu must remain account-blocked if it rejects the bounded request; this checklist does not authorize billing changes, quota resets, credential rotation or repeated provider tests.
<!-- janitor:end:todo -->
