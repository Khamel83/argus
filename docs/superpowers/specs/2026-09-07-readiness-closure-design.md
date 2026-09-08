# Argus readiness closure design

## Purpose

Close the remaining readiness evidence and repair the extraction provenance
defect without issuing additional provider requests or altering accounts,
budgets, credentials, or billing.

## Evidence rules

Every conclusion records the layer it proves: source, image, deployed runtime,
authenticated operation, provider outcome, durable receipt, or downstream
effect. A successful HTTP response, configured credential, or liveness check
does not establish end-to-end readiness by itself.

Existing provider canaries remain the only provider requests in this release.
This work will not retry Serper, enable or call Valyu, rotate credentials, or
run fresh paid-provider validation.

## Workstreams

1. Read the already-created Maya capture and page records by their exact
   identifiers through the native read-only path. Record only identifiers,
   hashes, statuses, timestamps, and cardinalities.
2. Repair extraction acceptance provenance so production records the admitted
   source identity and preserves the requested URL in the acceptance evidence.
   Add hermetic regression coverage, build a candidate image, and promote only
   through the root Homelab Compose procedure after its admission gates pass.
3. Diagnose the existing Serper 403 from saved, sanitized evidence and adapter
   contract only. Reconcile accounting from durable local evidence; label any
   provider-dashboard-only fact as unresolved rather than infer it.
4. Inspect browser-policy evidence and either produce a real external-policy
   attestation or record the exact missing authority/configuration. Never
   fabricate an attestation.
5. Review and repair bounded operational gaps: scorecard admission automation,
   deterministic service shutdown, and production gating for optional LLM
   gateway paths. Keep broad formatting/type churn out of this release unless
   a changed file requires it.
6. Update status, audit, context, todo, and affected specifications with a
   provider matrix that separates configured, attempted, outcome, receipt, and
   downstream evidence.

## Acceptance criteria

- The existing Maya record is either directly read back with exact evidence or
  has a reproducible, narrow blocker.
- Extraction evidence contains an admitted release identity and exact request
  URL under hermetic regression tests.
- No new provider request occurs.
- Serper and browser states are supported by recorded evidence or a specific
  reproducible block.
- Candidate promotion, if code changes, has source, image, runtime, and soak
  evidence recorded separately.
- Documentation names every provider as verified, blocked, unconfigured, or
  not revalidated, with the reason.
