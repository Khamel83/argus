# Argus Public Status

**Baseline:** September 1, 2026
**Package version:** 1.6.4

This page states the public audit baseline. It does not prove current service
state. Authorized maintainers can use the private
[argus-ops README](https://github.com/Khamel83/argus-ops/blob/main/README.md)
for the latest dated reports.

## Current continuation snapshot — September 7, 2026

The old `01cbd7d` / `sha256:b095bcab...` checkpoint is historical. The
currently deployed production pair is source
`458db1a10e158aa9ec156e8eaa85d6fbed2fe3e3` and image
`ghcr.io/khamel83/argus@sha256:1a7bba7a32ecd70f70e05e0fbc471ac58519c01c06c80ee30b688dce7b8eace4`.
The release receipt SHA-256 is
`337cb478905100c9bb881e6397116b8fa7a0a96ce4ab508e062323c2516a0cad`.
Both `current.json` and `known-good.json` name this pair; the previous
rollback target is the immediately preceding bridge image. No cutover marker
remains. The deployed package is still version `1.6.4`.

Required CI run `34107922118` passed on Python 3.11, 3.12, and 3.13, plus
the production-config, PostgreSQL-ledger, scorecard, freshness, and image
checks. The Python contract is not ambiguous: 3.11 is the supported package
floor, 3.12.3 is the canonical production runtime, and 3.13 is the supported
compatibility lane. The bounded readiness-lease-owner fix is in the deployed
source; long idempotency keys no longer cause the old HTTP 500.

The production containers `argus` and `argus-mcp` report healthy with zero
restarts. `/api/live`, `/api/health`, and `/api/ready` return HTTP 200.
Readiness is `ready=true` with `status=degraded`; `/api/readiness` does not
exist. The degraded reason set includes provider observations, browser, Maya,
and recovery. PostgreSQL is at schema `0011_extraction_spend_scope`, and the
runtime database role cannot create schema objects. Unauthenticated API and
MCP requests return HTTP 401; authenticated MCP discovery exposes the required
retrieval tools.

### Provider matrix

This table reports the latest explicit no-spend probes and the current policy
state. A successful free probe is evidence for that provider and path at that
time; it is not a guarantee that every query will work.

| Provider | Current state | Exact current evidence |
|---|---|---|
| SearXNG | Working, degraded | Authenticated no-spend probe returned 3 results; the aggregate remains degraded because upstream engines show CAPTCHA, access denial, suspension, or other failures. |
| DuckDuckGo | Intermittent, fail-closed | The latest explicit retry returned 3 results. A transient acquisition-policy block also occurred, and the broker correctly applied a short cooldown; this is not an API-key failure. |
| Yahoo | Working in the sampled path | Latest authenticated no-spend probe returned 3 results. The older 502 is not the current sampled result. |
| GitHub | Working in the sampled path | Latest authenticated no-spend probe returned 3 results. |
| Brave | Disabled: `not_registered` | Protected value is present, but credential-version and account-scope fingerprints are absent. No current call was authorized. |
| Tavily | Disabled: `not_registered` | Protected value is present, but credential-version and account-scope fingerprints are absent. No current call was authorized. |
| Exa | Disabled: `not_registered` | Protected value is present, but registration fingerprints are absent. No current call was authorized. |
| Linkup | Disabled: `not_registered` | Protected value is present, but registration fingerprints and account scope are absent. No current call was authorized. |
| Parallel | Disabled: `not_registered` | Protected value is present, but registration fingerprints and account scope are absent. No current call was authorized. |
| Serper | Disabled: `not_registered` | Protected value is present, but credential-version and account-scope fingerprints are absent. No current call was authorized. |
| You.com | Disabled: `not_registered` | Protected value is present, but registration fingerprints and account scope are absent. No current call was authorized. |
| Valyu | Disabled: `not_registered` | Protected value is present, but registration fingerprints and account scope are absent. No current call was authorized. |
| WolframAlpha | Disabled: `not_registered` | Application-id value is present, but its registration fingerprint and account scope are absent. No current call was authorized. |
| SearchAPI | Unconfigured | No key is present. |

“Protected value is present” is not the same claim as “the key is valid.” The
readiness registry fails closed until the operator records truthful non-secret
credential-version and account-scope bindings, finite budgets where required,
and approved no-spend evidence. The current no-spend run made no paid
provider calls.

The production ledger does contain 48 historical settled paid attempts from
July: Brave 6, Exa 13, Linkup 3, Tavily 25, and You.com 1. Their recorded
total is 48 Argus accounting charge units, not a claim of 48 US dollars. Those
historical rows prove only that those calls settled then; they do not prove
that the current secret versions work now.

### Extraction and downstream delivery

One complete live article extraction is now proven for the target page type:
the PEP 257 page was processed by `trafilatura`, returned 1,509 words,
passed quality, and recorded `is_complete=true`. It produced a durable Argus
receipt and an acknowledged Maya delivery bound to release identity
`argus-458db1a10e158aa9ec156e8eaa85d6fbed2fe3e3`. The Maya capture was
accepted with `duplicate=false`; the Argus outbox had 284 acknowledged rows
and zero dead letters after the operation. This proves that page and path,
not all pages or all extraction methods.

Browser capability is still not admitted: no current external browser-network
attestation exists, so the browser path remains fail-closed. Recovery remains
degraded because the metadata registry is incomplete. A current operational
Maya observation can expire under its short TTL even when the durable delivery
receipt remains valid; these are separate evidence layers.

## Historical baseline and remaining gates

The original audit score of **54/100** remains historical. It is not replaced
by the deployment or by an HTTP 200. Full production readiness still requires:

- truthful provider credential-version and account-scope registration, followed by approved no-spend tests;
- stabilizing DuckDuckGo's guarded egress/cooldown behavior and reducing SearXNG upstream failures;
- external browser-network attestation and one admitted browser observation;
- completion of recovery metadata-registry evidence; and
- a fresh audit run and readiness score.

The image workflow's automatic promotion also remains fail-closed when the
exact scorecard admission artifact is missing. This release was admitted and
promoted manually after the isolated free-only scorecard recorded accepted
residual risk because the pinned evaluator was unavailable.

Keep source, remote, image, deployed runtime, authenticated capability,
provider effect, durable write, and downstream receipt as separate evidence
layers. A test pass, a live check, or an HTTP 200 does not prove end-to-end
retrieval.

## Public Contribution

Use [CONTRIBUTING.md](../CONTRIBUTING.md) and local tests for source work. You
do not need private access for that work.
