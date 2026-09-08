# Argus status — September 7, 2026

Core HTTP/MCP access, extraction and Maya capture are usable in the sampled
paths. Full provider readiness is not established. The corrected image is
loaded and healthy. The 1,800-second soak passed; current and known-good
records name the corrected image. Valyu is disabled after its account rejection.

- Source: `8c9dad1356517cd01c714da84401aaed9242cc54`; package 1.6.4.
- Image: `ghcr.io/khamel83/argus@sha256:0536b56458b6b64592c6625a326d14a1d895e5561cdb10334788be875ac78812`.
- Release receipt SHA256: `9184554e2206dde22763ca1e4014e6ca00f94f81a312ac3e9dd74e990302f589`.
- PR136 required CI passed: Python 3.11/3.12/3.13, PostgreSQL-ledger,
  production-config, scorecard, freshness and image-build. Final local suite:
  **2,916 passed, 47 skipped, 4 warnings**. Independent review passed.
- Production Python 3.12.3; local development 3.12.13; package floor 3.11.
- PostgreSQL head 0011_extraction_spend_scope; runtime role cannot CREATE
  schema objects. Real disposable migration/no-drift and canonical restore
  checks passed; current recovery observation is healthy.

## Provider evidence

Every configured credential received one bounded authenticated authority
canary, with max_results=1, one key, no pagination and no retry. All thirteen
attempted providers have durable authorization/spend records. SearchAPI has
no key and was not called. Amounts below are Argus request-accounting units,
except Valyu's USD ceiling; provider-issued balances remain unknown.

| Provider | Last observed upstream outcome | Results | Accounting / limitation |
|---|---|---:|---|
| SearXNG |200 |1 |Settled zero monetary charge; bridge image sample |
| DuckDuckGo |Guarded policy block |0 |Zero charge; public-policy repair tested, not re-probed |
| Yahoo |200 |1 |Settled zero monetary charge; bridge image sample |
| GitHub |200 |1 |Settled zero monetary charge; bridge image sample |
| Brave |200 |1 |One request unit settled; bridge image sample |
| Tavily |400 |0 |One unit uncertain; POST framing fixed afterward, not re-probed |
| Exa |400 |0 |One unit uncertain; POST framing fixed afterward, not re-probed |
| Linkup |400 |0 |One unit uncertain; POST framing fixed afterward, not re-probed |
| Parallel |411 |0 |Approved primary account; one unit uncertain; framing fixed afterward |
| Serper |403 |0 |Access rejected on corrected image; cause not established; one unit uncertain |
| You.com |200 |1 |One request unit settled; bridge image sample |
| WolframAlpha |200;2+2=4 |1 |Zero monetary charge; provider quota balance not returned |
| Valyu |402 |0 |Blocked by account; USD0.0015 reservation uncertain; no billing mutation |
| SearchAPI |Unconfigured |0 |No key, registration or request |

“Bridge image” means source d1e5594/image 45c56b8, immediately before the
corrected 8c9dad1/0536b564 image. Those receipts are retained as samples, not
silently relabeled as post-repair validations. All nine supplied credentials
are registered and their runtime values match the private vault. Registration
and enablement do not prove authentication or retrieval. The full private
matrix records enablement, keys, attempts, receipts and consumer effects.

## User operation and remaining limits

Authenticated MCP initialization, tool discovery and an HTTP-authority health
tool succeeded both inside MCP and from the Mac through Tailscale TLS.
Unauthenticated MCP returned 401. The MCP container has no provider credentials
or database URL. These read-only checks made no provider request.

One fresh free-only PEP 257 extraction returned 1,509 words, quality_passed=true,
is_complete=true and a durable PostgreSQL receipt. Maya acknowledged its
single-page capture in one attempt. This proves capture ingestion; it does
not prove later embedding or memory use. The fifteen-second Maya observation
has expired while its durable acknowledgment remains valid.

Two evidence defects remain explicit: this extraction's native release field
is unknown-release (the actual image was separately checked), and its returned
URL was shortened to the PEP site root. Neither field was rewritten. New
provider-probe release binding is fixed and verified for Serper/Valyu; the
extraction configuration/binding gap remains a follow-up.

External browser access remains unsupported without a real browser-network
attestation. Production rejects it before dispatch. The isolated browser
startup result is not external browsing proof. Readiness remains degraded,
not fully healthy. See [TODO.md](../TODO.md) for bounded remaining work.

The replacement audit judgment is **75/100**, up 21 from the historical 54/100:
security 15, live core 12, data/recovery 16, tooling 17, operational wiring 15
(each out of 20). This is an evidence-based editorial assessment, not an SLA
or a numerical guarantee. Full private evidence remains in argus-ops.
