# Argus Managed-Stack Evaluation

**Snapshot:** 2026-08-09
**Status:** Final decision recorded — **narrow Argus plus a time-bounded
30-day experiment**. The final Argus acceptance is **FAIL** at 72/100 because
hard gate 7 failed; no managed vendor is promoted or ranked from this record.
**Evidence base:** the pre-acceptance
[Argus market landscape](2026-08-09-argus-market-landscape.md), plus the final
[Argus acceptance report](2026-08-09-argus-tonight-acceptance.md) and its
remotely read run `0f30946aa4fb`.

This document separates documented vendor surfaces from architectural
inferences and from empirical results. The market landscape is a dated research
input, not proof of current production success, pricing, eligibility,
residential execution, or Argus parity. The acceptance pack is the only basis
for the final Argus score; market evidence cannot raise it.

## Decision question and exact benchmark frame

The design's later block labeled “Frozen benchmark” is reproduced exactly
below as the managed-stack comparison frame:

```text
Question: As of 2026-08-09, should a small self-hosted AI-agent stack use
Parallel plus Bright Data, Linkup plus Firecrawl, or a narrowed Argus gateway
as its default deep-research acquisition path?

Decision: choose what Argus should retain, replace, or postpone for the next
30-day activation experiment.

Scope: public product documentation, pricing, API/extraction/search/browser
capabilities, data-handling statements, and Argus's measured operational
contract. Exclude vendor marketing claims that cannot be tied to an official
source or independently observed artifact.

Constraints: no one-time credits, no uncapped paid calls, no private source
material, and no purchase or account change tonight.
```

The design also specifies a first-production invocation with a broader
keep/narrow/replace question. That broader block is the authoritative workflow
input and is reproduced in the acceptance report. The narrower block above is
the post-run decision-synthesis frame: it is applied only after the Argus
evidence pack and score are frozen, and it cannot repair or raise them.

## What is documented versus inferred

### Dated documented findings from the market landscape

- No reviewed single product documents the complete Argus contract of broad
  search, deep research, robust extraction, interactive stealth browser,
  explicit residential egress, source and execution provenance, per-caller
  policy, durable normalized outcomes, and HTTP/MCP integration.
- Parallel documents search, extraction, Task/Research, and claim-level Basis;
  Bright Data documents managed browser/Web Unlocker, CAPTCHA handling, and
  residential/global egress.
- Linkup documents search, fetch, research, JavaScript rendering, MCP, and a
  restored monthly balance; Firecrawl documents search/scrape/crawl/browser
  interaction, MCP, and an automatic stealth retry. The reviewed Firecrawl
  material does not establish that stealth is residential.
- Exa documents search, contents, deep search/reasoning, research agents,
  citations, cost telemetry, and hosted MCP; Browserbase documents browser
  sessions, CAPTCHA handling, stealth, residential proxies, Search/Fetch, and
  MCP.
- The market landscape states that managed products are stronger at answer/source
  provenance than at Argus's full execution provenance (provider, extractor,
  egress, machine, skip/failure state, and policy/budget decision).

These are summaries of the dated report, not independently refreshed claims.
See its linked primary sources before procurement or account changes.

## Verified Argus operational baseline before the scored comparison

The following are verified remediation, regression, delivery, client, and
candidate-release facts. They establish Argus's measured operational contract;
they do not establish managed-vendor parity.

| Surface | Verified evidence |
|---|---|
| Argus remediation/regression | PR #114 merged at `8ea82f`; PR #115 merged at `873cd`, with full-suite result 2,308 passed / 43 skipped; PR #116 merged at `15d566e`, with full-suite result 2,309 passed / 43 skipped; PR #118 merged at `36f4f753568b53a4d492ae97bcd2035a8aa02f1a`; PR #119 merged at `29f40a6` with all checks green and transport hardening. Final local PR #119 suite: 2,339 passed / 43 skipped. |
| Homelab remediation | Configuration PRs #139–141; accounting PRs #142–144; scorecard runner PR #145; transport PR #146; PR #147 merged at `f7309e`. |
| Maya remediation | Maya PR #160 merged at `b4726e9`. |
| Canonical clients | Codex, Claude Code, and OpenCode passed fresh login-shell checks against `https://homelab.deer-panga.ts.net:8443/mcp`, using runtime environment references; Gemini remained disabled. |
| Authority and caller policy | Evidence authority is configured with caller caps `clio*:1,hermes*:1,mac-agents:1,maya:1`; You Contents is disabled (`false`). |
| Maya delivery | All 274 delivery intents are acknowledged; pending, retry, and dead-letter counts are each 0. The pre-canary baseline was 275 captures / 181 pages; the direct canary added one durable parent and zero pages, leaving 276 / 181. Unique idempotency was verified, and exact replay produced one row. |
| Benchmark candidate and restored runtime | Candidate version `1.6.3`, source `36f4f753568b53a4d492ae97bcd2035a8aa02f1a`, image `ghcr.io/khamel83/argus@sha256:d8fcf18f75adf8db07401582b56fb611bed20aba9a0f86a93733c0fe1ebd40fb`, deployment `argus-36f4f753568b53a4d492ae97bcd2035a8aa02f1a`. Candidate promotion completed at `2026-08-10T12:05:31Z`; both containers were healthy with zero restarts; GitHub attempt 3 succeeded. After the failed benchmark, the supported promoter restored current/known-good to image `ghcr.io/khamel83/argus@sha256:dbe4d81a9af3c3ea608600d0be4ea759116c19ac2f80d9ba802f9465a5a81257`, source `d11c2d05bd8b848f3c52ec3255c5f76cf8107dd6`, deployment `argus-d11c2d05bd8b848f3c52ec3255c5f76cf8107dd6`, and receipt SHA-256 `7add2ab6043362b53a32db280bb357a7ab4cf45c7ab5ae186f6ac7a24b56b81d` at `2026-08-10T13:29:33.163465Z`; the full soak passed and both services were healthy with zero restarts. |

Two promotion incidents remain part of the reliability record: the first
immutable 1.6.3 attempt hit a stale accounting gate that counted a superseded
legacy table; its rollback hit the same stale assertion until the exact v2
receipt/evidence-plan accounting assertion and PostgreSQL/JSON invocation fixes
were regression-tested. A later rollback failed over SSH/Tailscale with an
EPIPE transport error; startup reconciliation and the durable-log fix were
subsequently verified. These incidents are not benchmark scores and do not
promote the prior excluded `0ca3` runtime or any new candidate to known-good.

### Excluded workflow attempts (not scored)

The acceptance report retains both failed attempts and the later excluded
completed attempt; none supplies a keep/narrow/replace result:

- `d7e045586269` failed with `InvalidRetrievalPlan` after a 120-page crawl
  limit reached the broker's separate 50-result search field. PR #116 clamps
  that base search to 50 while preserving the 120-page discovery limit. It
  produced no paid-provider delta and is excluded from scoring.
- `7140464de88a` failed at `2026-08-10T02:07:03.398947` with
  `workflow_composition_extraction_failed`; status showed image `0ca3`/source
  `15d`, zero public sources/domains/primary sources, no report or manifest,
  tier-0 free usage `+7` and `$0`, and no outbox delta. Later artifacts had
  five usable plus one partial result, but rank-0 required-extraction
  poisoning and terminal mappingproxy state persistence invalidated the run;
  PR #117 fixed both defects. It is excluded from scoring.
- `0701607eaca6` completed in `89.100247` seconds with HTTP/MCP equality. The
  report hash is
  `89a0a417fe9d600c7f5786ae9b761487a3149557c187ce1fc91de7d6764c82c1`
  (4,728 bytes) and the manifest hash is
  `0c682048d955383b583bcbdaab39e17b02fd30080379d9f7ec5abb0ee8d1b148`
  (12,569 bytes). Counts were 11 sources / 7 domains / 4 primary, with an
  independent usable count of 9 / 7 / 3 including the official source; usage
  was tier-0 free `+11`, `$0`, and outbox delta `0`. It is excluded because
  manifest runtime fields were all unknown while status reported exact image
  `dbe4` and source `d11c`; that mismatch triggered PR #118.

These observations are reliability evidence only. They do not populate the
empirical comparison ledger or establish a final Argus score or recommendation.

### Final acceptance evidence and score

The canonical final run was `0f30946aa4fb`, completed at
`2026-08-10T12:43:44.369436Z` in `100.449463s`. Its report SHA-256 is
`6eab802f23f1b44e6700b4520a88e5b9f145be526ee31127f299f3bbe24bfd64` (4,728
bytes); its manifest SHA-256 is
`0c1b03aaf40bc231fa4a1b72e324a02a59117cafedd6d4470155feaebe561973` (12,726
bytes). The pack contains 11 sources / 7 domains / 4 primary; an independent
audit found 9 usable / 7 domains / 3 usable primary and official, with S1 and
S8 partial. The manifest declares `cost_state=unavailable`; the operational
ledger nevertheless records tier-0 free `+11`, `$0` reserved/actual, no paid or
unresolved charge, and an unchanged outbox of 274 acknowledged / 0
pending/retry/dead. This cost distinction is intentional and is not a claim of
universal zero cost.

The direct canaries made exactly three POSTs: Argus used free GitHub and
returned `proven_empty`; Maya returned 201 and then exact replay 200 with one
durable capture/key and zero pages. A helper positional-argument bug was
recovered read-only, with no extra POST. Codex, Claude, and OpenCode connected
to canonical HTTPS; Gemini remained disabled. Evidence authority caps were
`clio*:1,hermes*:1,mac-agents:1,maya:1`, and You Contents was `false`.

The frozen eight-gate result was gates 1–6 and 8 **PASS**, gate 7 **FAIL**:
the numeric source floor passed, but the report's claimed `/web-research` URL
is absent from the manifest citation set. Insufficient vendor-primary coverage
is a separate score deduction. The independent fixed score is
**72/100** (16/25 source, 8/15 coverage, 12/15 discipline, 13/15 usefulness,
14/20 execution, 9/10 provenance/cost), so the literal Argus acceptance result
is **FAIL**. The external synthesis `decision-synthesis.md` (SHA-256
`c12ff726f37f5fe00b14cb6fa72c95339cc0c3ea98d6f4b0f660d9efb4131b55`) uses only
the report and manifest and recommends **narrow Argus plus a time-bounded
30-day experiment**. It cannot repair missing evidence or use the separate
market landscape to inflate the Argus score, and it does not rank the managed
vendors.

### Architectural inferences (not measured outcomes)

The market landscape's fit bands are explicitly estimates: Parallel + Bright
Data 90–95%, Linkup + Firecrawl 85–95%, Exa + Browserbase 90–95%, and Brave +
Firecrawl 75–90% for ordinary outcomes before a thin control layer. They do not
claim parity on Argus audit controls or success against this installation's
hard targets. The same report's least-regret hypothesis is to shrink Argus to
a thin control plane over two managed services, benchmark it, then retire it
only if source citations and vendor budgets prove sufficient.

## Candidate comparison (pre-benchmark)

| Candidate | Documented strengths | Documented gaps / uncertainty | Current decision posture |
|---|---|---|---|
| Parallel + Bright Data | Low-cost search/extraction/research plus Basis; managed hard-page/browser, CAPTCHA, geo, and explicit residential paths. | No common cross-vendor policy, normalized execution trace, or durable Argus evidence contract documented. | **PENDING empirical comparison**; leading hypothesis in the dated landscape. |
| Linkup + Firecrawl | Search/fetch/research with recurring balance; crawl/scrape/browser/MCP and stealth retry with public free credits. | Firecrawl stealth is not documented as residential; execution provenance and cross-vendor budget semantics remain unproven. | **PENDING empirical comparison**; value-oriented hypothesis. |
| Exa + Browserbase | Search/content/deep research/citations/cost telemetry plus browser, stealth, CAPTCHA, residential, and MCP. | No automatic evidence join, common budget model, or durable execution-provenance contract documented. | **PENDING empirical comparison**; developer-experience alternative. |
| Narrowed Argus gateway | Caller identity, tier caps, spend reservation, normalized outcomes, source/execution provenance, durable SQL, and HTTP/MCP equivalence are measured operational strengths; the benchmark candidate identity, restored runtime, delivery canary, clients, policy, and regression evidence above are verified. | Acceptance scored 72/100 and gate 7 failed; hard-page wins, vendor parity, and whether callers consume the governance semantics remain unmeasured. | **NARROW + 30-day reversible experiment**. |

## Decision criteria

The next decision must use the same criteria for each candidate and preserve
facts, calculations, inferences, and unknowns separately:

1. **Research utility and citation integrity:** useful answer/page outcomes,
   source diversity, primary-source share, citation resolution, and completeness.
2. **Difficult-page execution:** success on the representative hard-target
   corpus; browser interaction, CAPTCHA handling, geography, residential path,
   and explicit execution evidence. A “stealth” label alone is not residential
   proof.
3. **Governance and spend:** caller attribution, tier/credit caps, reservation
   behavior, actual provider attempts, marginal/total cost, unresolved charges,
   and ability to prevent uncapped or one-time-credit calls.
4. **Provenance and durable evidence:** provider/extractor/egress/machine/source
   metadata, skip/failure reasons, normalized result shape, artifact hashes,
   durable acceptance, and HTTP/MCP equivalence.
5. **Privacy and data location:** documented retention/ZDR, cache behavior,
   data processing, deployment location, and whether the requirement permits
   external vendor handling. Unknowns remain unknown.
6. **Operating burden and resilience:** integration count, upgrades, browser/
   proxy maintenance, observability, failure transparency, recovery, latency,
   and rollback complexity.
7. **Recurring/free-credit fit:** current documented allowances and eligibility,
   treated as time-sensitive and not as procurement quotes.
8. **Interoperability and reversibility:** HTTP/MCP compatibility, caller
   migration effort, adapter ownership, portability of evidence, and ability to
   retain a narrowed control plane.

## Empirical comparison ledger

The following fields are required before a final keep/narrow/replace/experiment
decision. They are deliberately **PENDING** and must be populated from the
frozen acceptance pack, a representative shadow run, and redacted operational
snapshots—not estimated from vendor pages.

| Measure | Parallel + Bright Data | Linkup + Firecrawl | Narrowed Argus | Evidence |
|---|---|---|---|---|
| Usable source count/domain diversity | **PENDING** | **PENDING** | **PENDING** | Run manifests |
| Primary-source share/citation resolution | **PENDING** | **PENDING** | **PENDING** | Citation audit |
| Hard-target success/completeness | **PENDING** | **PENDING** | **PENDING** | Golden corpus |
| Residential/browser execution proof | **PENDING** | **PENDING** | **PENDING** | Provider traces |
| Median/p95 latency | **PENDING** | **PENDING** | **PENDING** | Timestamped runs |
| Marginal and total cost | **PENDING** | **PENDING** | **PENDING** | Spend ledger; uncertainty labeled |
| Failed/skipped/degraded transparency | **PENDING** | **PENDING** | **PENDING** | Outcome traces |
| Caller policy and budget enforcement | **PENDING** | **PENDING** | **PENDING** | Auth/policy canaries |
| Durable artifact/provenance completeness | **PENDING** | **PENDING** | **PENDING** | Report/manifest/schema audit |
| Operating effort and recovery | **PENDING** | **PENDING** | **PENDING** | Runbook/incident log |
| Decision result | **PENDING** | **PENDING** | **NARROW + 30-day experiment** | Human review; vendor scores remain unmeasured |

## Decision outcomes

- **Keep:** retain broad Argus only if live evidence shows material recall,
  diversity, hard-page, cost-control, or provenance value that candidates do
  not provide, and the burden is justified.
- **Narrow:** retain a thin authority/gateway, normalized evidence model,
  caller and spend policy, durable usage/outcome store, and a small scorecard;
  buy volatile search, extraction, browser, and residential machinery.
- **Replace:** use a managed product or pair directly when useful answers/pages
  and source citations are sufficient, account-wide budgets are acceptable,
  protected-site misses are tolerable, and external processing/lock-in are
  allowed.
- **Time-bounded experiment:** if evidence is incomplete or conflicting, run a
  30-day shadow comparison with no irreversible adapter deletion or account
  change. Record utility, source diversity, hard-page success, latency, cost,
  citation quality, and failure transparency.

**Current outcome:** **NARROW** Argus to the governance boundary and run a
time-bounded **30-day reversible experiment**. Parallel + Bright Data, Linkup
+ Firecrawl, and any other managed default remain unranked: their empirical
scores are **PENDING**, and the market landscape cannot raise the Argus
acceptance score. No purchase or account change is authorized.

## Required follow-up evidence and limits

- Acceptance hard gates and the 100-point score: see
  [Argus Tonight Acceptance](2026-08-09-argus-tonight-acceptance.md); the final
  result is **FAIL**, score 72/100, with gate 7 failed.
- Re-verify pricing, promotional eligibility, model lifecycle, privacy terms,
  cache defaults, and residential attribution before procurement.
- Do not infer target success from “stealth,” “unlocker,” “undetected,” or a
  public MCP listing. Do not infer Argus value from repository intent alone.
- No purchase, account change, one-time-credit call, or private-source use is
  authorized by this report. The 30-day experiment must use one fixed corpus,
  fixed limits, and explicit cost/provenance measurements before any default
  path is selected.
