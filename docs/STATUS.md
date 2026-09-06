# Argus Public Status

**Baseline:** September 1, 2026
**Package version:** 1.6.4

This page states the public audit baseline. It does not prove current service
state. Authorized maintainers can use the private
[argus-ops README](https://github.com/Khamel83/argus-ops/blob/main/README.md)
for the latest dated reports.

## Current continuation snapshot — September 6, 2026

The production authority is running release 1.6.4 from source `01cbd7d` and
image digest `sha256:b095bcab...`. PostgreSQL is at schema
`0011_extraction_spend_scope`. Recovery evidence is current, bound to that
source and image, and the promotion gate is healthy.

Fresh authenticated checks produced free DuckDuckGo results over HTTP and
MCP. A complete article extraction was accepted, and a bounded browser
lifecycle canary passed with no OOM events or orphan runtime processes.

A reviewed Argus-to-Maya canary received HTTP 201 with durable capture receipt
`23ddb0123b0a4b6a8f6588803a786529`. This is a current receipt for the direct
Maya operation; it does not make the general extraction or provider matrix
ready.

The current operational state remains `ready=true, degraded`. SearXNG still
returns valid empty responses because its upstream engines are blocked or
suspended. Yahoo still returns an unrecognized response to its parser.
Credentialed provider values are present for some paid providers, but their
credential-version and account-scope fingerprints are not registered. No paid
provider call was made. The browser lifecycle canary passed, but the
post-restart runtime observation has not yet been admitted into the authority
status cache.

## Evidence

Free search and the AI connection returned results on August 30. Those dated
tests do not prove a current provider response.

One stored page response gave limited proof for webpage extraction. It does not
prove general extraction works now.

Argus includes 14 adapters. An adapter is code that connects Argus to a search
service. An installation does not always make all adapters available.

## Limits

- Do not use Argus as a safety boundary for untrusted URLs. General extraction needs repair before that claim is safe.
- Cost records for paid extraction do not yet prove each charge. Keep affected methods disabled.
- Some extraction methods remain disabled. Domain-hinted extraction needs repair.
- Do not rely on workflows to keep data separate between users. This repair is not complete.
- Audit restore verification did not pass. A backup alone does not prove restore.
- Downstream delivery is unverified. A receipt from the target must prove delivery.

The remaining readiness gates are: register truthful provider fingerprints and
account scopes; repair or replace blocked SearXNG upstream engines and Yahoo
egress/parser behavior; admit a current browser runtime observation; and issue
a refreshed audit score after those gates are independently evidenced.

Keep source, remote, image, deployed runtime, authenticated capability,
provider effect, and downstream receipt as separate evidence layers. A test
pass, a live check, or an HTTP 200 does not prove end-to-end retrieval.

## Public Contribution

Use [CONTRIBUTING.md](../CONTRIBUTING.md) and local tests for source work. You
do not need private access for that work.
