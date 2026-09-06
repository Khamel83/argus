# Argus Public Status

**Baseline:** September 1, 2026
**Package version:** 1.6.4

This page states the public audit baseline. It does not prove current service
state. Authorized maintainers can use the private
[argus-ops README](https://github.com/Khamel83/argus-ops/blob/main/README.md)
for the latest dated reports.

## Current continuation snapshot — September 6, 2026

The old `01cbd7d` / `sha256:b095bcab...` checkpoint is historical. The
continuation is promoted by immutable source and image identity; read the
current pair from authenticated `/api/admin/status` and the private promotion
receipt. PostgreSQL is at schema `0011_extraction_spend_scope`, and the
production runtime contract is **Python 3.11 floor, Python 3.12 canonical,
Python 3.13 compatibility**. The deployed homelab image runs Python 3.12.3;
CI passes all three supported interpreters.

Current free search is proven through SearXNG. The homelab configuration now
enables the observed-good Bing and Yandex engines, with a rollback copy of the
previous SearXNG settings. An authenticated Argus search returned five results
through SearXNG after that repair. Other SearXNG upstreams remain individually
blocked, challenged, suspended, or intermittent; SearXNG is therefore useful
but still a degraded aggregate.

One complete article extraction is proven for the Python PEP 20 page: the
post-release run used `trafilatura`, returned 229 words, passed the quality
gate, and recorded `is_complete=true`. This proves that target page, not every
page or every extraction method. The browser chain is not admitted: production
has no external browser-network attestation provider, so browser requests fail
closed with `browser_policy_unavailable` before a browser can be created. A
separate lifecycle canary is not authority admission.

The current direct Maya canary received HTTP 201 with durable capture receipt
`23ddb0123b0a4b6a8f6588803a786529`. The dispatcher observation is still stale
and no receipt is yet bound to the continuation release, so Maya is not a
general readiness pass.

The operational state remains `ready=true, degraded`. `/api/ready` is the
correct readiness route; `/api/readiness` does not exist.

### Provider matrix

| Provider | Current state | Reason or proven effect |
|---|---|---|
| SearXNG | Working, degraded | Five-result live search after enabling observed-good Bing/Yandex; several upstream engines still fail. |
| DuckDuckGo | Not currently proven | The latest live probe was blocked by acquisition policy; an older free result is historical. |
| GitHub | Not currently proven | Latest live probe was empty/parse-unstable. |
| Yahoo | Not usable | Upstream alternates between HTTP 500 and HTML that does not satisfy the parser contract. |
| Brave | Not admitted | Key is present, but credential-version and account-scope fingerprints are absent; no call was made. |
| Tavily | Not admitted | Key is present, but credential-version and account-scope fingerprints are absent; no call was made. |
| Exa | Disabled | Disabled in production configuration after prior failures; key presence does not enable it. |
| Linkup | Not admitted | Key is present, but registration metadata and a finite budget are absent; no call was made. |
| Parallel | Not admitted | Key is present, but registration metadata and a finite budget are absent; no call was made. |
| Serper | Not admitted | Key is present, but credential-version and account-scope fingerprints are absent; no call was made. |
| You.com | Not admitted | Key is present, but registration metadata and a finite budget are absent; no call was made. |
| SearchAPI | Unconfigured | No key is present. |
| Valyu | Not admitted | A protected value is present, but registration is absent; no call was made. |
| WolframAlpha | Unconfigured | No application key is present. |

“Key is present” is not the same claim as “key is valid.” The readiness
registry now fails closed until the operator records truthful non-secret
credential-version and account-scope bindings, a finite budget where required,
and the approved no-spend evidence. No paid provider call occurred.

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
account scopes; repair or replace Yahoo egress/parser behavior; provide an
external browser-network attestation and admit one current browser observation;
bind a Maya receipt to the continuation release; and issue a refreshed audit
score after those gates are independently evidenced. SearXNG is no longer an
empty aggregate, but it remains degraded until its upstream failure mix is
reduced.

Keep source, remote, image, deployed runtime, authenticated capability,
provider effect, and downstream receipt as separate evidence layers. A test
pass, a live check, or an HTTP 200 does not prove end-to-end retrieval.

## Public Contribution

Use [CONTRIBUTING.md](../CONTRIBUTING.md) and local tests for source work. You
do not need private access for that work.
