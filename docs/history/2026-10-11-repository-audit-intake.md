# Repository audit — intake coverage and classification (October 11, 2026)

Dated evidence for the [repository reconciliation audit #192](https://github.com/Khamel83/argus/issues/192)
under coordinator [#191](https://github.com/Khamel83/argus/issues/191).
Canonical plan: [docs/REPO_PLAN.md](../REPO_PLAN.md).

Historical record. Do not invent evidence; unreadable/missing inputs stay
unknown. A merged PR alone is not runtime acceptance.

## Captured source revisions

- Manifest: `docs/reconciliation/intake-2026-10-10.json`
  - `repository`: `Khamel83/argus`
  - `source_base`: `cf20dd6eceba8aa67902166d1c044193104a3a5f`
  - `captured_at`: `2026-10-11T09:22:08.426397+00:00`
  - `retained_records`: 171; `open_records`: 16
  - `coverage`: "Full open issue/PR bodies and open issue comments; closed body
    excerpts since 2026-07-12. Other review/deployment evidence may be missing."
- This worktree HEAD is `cf20dd6`, matching the captured `source_base`.

## Readable inputs (all read)

Every record part was read and its SHA-256 recomputed against the manifest
`record_parts` entries; all eight match.

| Part | Records | SHA-256 (verified) |
|---|---:|---|
| intake-2026-10-10-part-1.json | 29 | `ea165bab176548af6b26e7655d2b5d61fc1788e19fc4900210b6675c734ddffc` |
| intake-2026-10-10-part-2.json | 19 | `0947e30185b8219424912c6ff270e066dfc4d024a493c1362f0f27cf09f536c5` |
| intake-2026-10-10-part-3.json | 17 | `367afaefc0ad572c28d7a3c8b41663e9562a5b391aac910142614fe9d85a6998` |
| intake-2026-10-10-part-4.json | 26 | `79d263c8ad85775a0269c80c52632b4dd4fc1d7a8b438783403d5f1fccce3358` |
| intake-2026-10-10-part-5.json | 24 | `5a99d856a1affe8f2de0f111b3805cff10847c1e8060602c64ea895e88db8ae1` |
| intake-2026-10-10-part-6.json | 22 | `f0cbd4e735ef710110f1d5e43ae1946f3b24c6c0d04a7694854d995c8af36657` |
| intake-2026-10-10-part-7.json | 22 | `cd6af0b680a80b3c7da60176d09d50d9a6db3e014ea231b2029d7dc94bf83d96` |
| intake-2026-10-10-part-8.json | 12 | `7ff7ed8c4ea21e88df15c933593065ede89549d6d967a2f3f69bda0ba52384d1` |

Total 171 records. Other repository docs read for this audit: AGENTS.md (no
CHARTER file exists), TODO.md, CONTEXT.md, handoff.md, INFRA.md, docs/STATUS.md,
docs/roadmaps/argus-retrieval-platform.md, docs/superpowers/plans/2026-09-07-readiness-closure.md,
`.github/workflows/ci.yml`, `pyproject.toml`, `.python-version`.

## Omitted / unreadable inputs

The manifest lists 20 `omitted` records, all with reason "closed before bounded
history window":

- Issues: 5, 7, 9, 12, 13, 15, 18, 19, 20
- Pull requests: 1, 2, 3, 4, 6, 8, 10, 11, 14, 16, 17

Their bodies are not in the packet and are not treated as completed. Issue 146
does not appear in `native_ids`; it is not a record. `merge_identity` is
"unknown unless separately verified" for all parts, so merge claims here rest on
the manifest's `merged_at` fields and repository docs, not on verified merge
commits.

## Objective repository facts checked

- No `Makefile` and no `mise.toml` exist at the repository root
  (`.python-version` = `3.12`; `pyproject.toml` has no ruff/mypy/pyright config).
- `.github/workflows/ci.yml` runs jobs `test`, `scorecard`, `postgres-ledger`,
  `production-config` using `uv` directly; there is no `lint`/`typecheck` job.
- An optional workflow LLM gateway is referenced only inside
  `docs/superpowers/plans/2026-09-07-readiness-closure.md`; no such module was
  located in this repository.

These facts drive the premise-mismatch and readiness findings in the plan.

## Classification evidence

### Open issues (16)

- #191 coordinator — open, `g2k:parked`; tracking-only.
- #192 audit — this task.
- #151 scorecard concurrency — open bug (`bug,high-priority,chronos,infrastructure`);
  decomposed into #154–#158 per its own comment; parent tracks.
- #154–#158 scorecard children — open, `needs-human`; sequential chain.
- #177 mise adoption — open; body states blocked by Khamel83/infra#727.
- #179–#183 mise children — open, `needs-human`.
- #148 paywall login browser — open, `needs-human`; #149 scoped exception merged
  `2026-09-14`, live NYT/WSJ acceptance unmet; long-term attestation absent.
- #159 AI chat share links — open, `needs-human`; complete acceptance, browser
  cross-domain admission dependency.

### TODO.md

Completed with packet evidence: H26 observer implementation/review (PR #173,
merged `2026-10-02`); PR #166 spend-clock race repair; required CI on `b8642be`;
`oci-ts` AI Review retirement (PR #163, merged `2026-09-03`); managed-pointer
reconciliation (PR #189, merged `2026-10-10`). The pre-existing `[x]`
restoration lines are completed historical work.

Corrected in TODO.md this audit: the H26 "exact-head review and required CI"
line was still open but is satisfied by the PR #173 merge (CONTEXT.md "Recent"
records the merge after filesystem identity, malformed-input, and
integer-attempt corrections).

Remaining/blocked TODO items: H26 guarded release and Homelab binding gates;
natural Janitor observation; browser-network authority; provider re-validation
and charge reconciliation; scorecard-admission handoff automation; shutdown/
finalization debt; LLM-gateway review; type/format governance; natural publisher
verification; extraction-completion conflict.

### Closed records checked for claimed completion

- #185 closed `2026-10-06`, labels `needs-human`, no captured comments, no merge.
  Its content is a real-browser CDP plan that overlaps [#148](https://github.com/Khamel83/argus/issues/148)
  item 2, and its stated acceptance (one NYT and one WSJ article through
  `extract_content_v2`) is not evidenced in the packet. Classified
  **unknown/duplicate**; #148 stays open.
- #149 merged `2026-09-14`; its own live acceptance checkbox is unchecked.
- #138 merged `2026-09-08`; model-matched to a TODO line whose archived generated
  counterpart stayed unchecked — conflict preserved as unresolved (Wave 6).
- Doc-reconcile PRs #147–#190 closed/merged routinely as `docs:` maintenance.
- Star-opportunity issues #139–#145 closed `2026-09-23` as parked/declined.
- Wayfinder issues #21–#103 and their PRs completed the P1 rollout (historical).

## Report of coverage limits

- Closed-issue acceptance beyond the excerpt window and any review/deployment
  receipts outside the packet remain unknown.
- No credentials, secrets, logins, or files outside this worktree were
  accessed. No native issue was created, closed, re-labeled, or enqueued.
