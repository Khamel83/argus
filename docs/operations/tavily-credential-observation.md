# Tavily credential metadata observation

This is Homelab H26's first owner-produced observation. It is source work,
disabled by default. It does not establish aggregate credential health or
change search routing, budget enforcement, account policy or public readiness.

The existing authority probe loop runs every 30 minutes. A typed
`ARGUS_TAVILY_USAGE_OBSERVER_ENABLED=false` opt-in controls the additional
observer. False means no provider request or receipt write. When enabled,
the observer uses the existing Tavily config and credential in process; it
does not invoke the old balance checker or give Homelab provider credentials.

The fixed authenticated [Tavily usage endpoint](https://docs.tavily.com/documentation/api-reference/endpoint/usage)
returns key/account metadata. Official documentation checked October2 describes
a GET and a [10-request-per-10-minute limit](https://docs.tavily.com/documentation/rate-limits).
This operation performs no model inference or search. The observer makes at
most one request per run, with verified TLS, no redirects/environment proxies,
a five-second connection bound, ten-second total deadline and64KiB body bound.
Raw responses, account/plan names, usage numbers, credentials and free-form
errors are excluded from receipts and logs.

One latest redacted `argus.credential-observation/v1` receipt lives under
the existing data root at `credential-health/tavily-usage.json`. It contains
source/definition identity, run/time, attempt count and typed field outcomes.
Only structurally admitted metadata can verify authentication and metadata
availability. Budget metadata does not authorize spending or prove remaining
capacity. Identity, scope, capability, decryptability and general availability
remain unknown. Overall status remains unknown. Missing optional credentials
are explicit `not_configured`;401,403 and429 have distinct meanings.
Credential version remains unknown: no secret-derived fingerprint is made.

The dedicated directory is owner0755, latest file owner0644 and single-link.
Directory locking, trusted metadata, bounded reads, CAS and atomic replacement
protect publication. Pre-rename failures preserve the prior latest;
post-rename durability failures remain unknown. Duplicate suppression must
validate receipt schema/hash/source/definition and never renew timestamps.
This latest-only slice provides no immutable history or notification delivery.

At October2 inspection, production remains Homelab image
`sha256:c8853fbe0d95d44f52bf68b0f5e579013c89bb07d722effa0abbe14e7f2df7df`,
source `e8f36cfea3552a952479b335f964618f89f4fd42`, UID/GID1002.
Its existing `/data` bind is `/mnt/main-drive/appdata/argus`. Four source
overrides remain protected. No new mount, credential, daemon or privilege is
needed for this observer. Do not replace the running image outside the existing
digest/source admission, backup/restore, candidate, rollback and1,800-second
promotion-soak procedure.

The heartbeat UID1000 cannot read root-owned Argus promotion state. Its current
v1 receipt remains unchanged. A later reviewed protected capsule must supply
an independently trusted expected Argus source and definition; the reader must
not trust a receipt's self-asserted source. Separate acceptance gates are exact
source review/CI, guarded release and explicit opt-in, a natural producer run,
credential-free reader acceptance, durable health-log admission and downstream
notification evidence. Do not close Homelab H26 from source tests alone.
