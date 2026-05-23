# Handoff: Multi-Egress Worker Architecture

**Created**: 2026-05-22 23:07
**Context Used**: ~85% when created

## Quick Summary

Designed and fully planned a multi-egress routing system for Argus. The problem was Yahoo Search blocking homelab's Spectrum residential IP (23.241.236.110) while oci-dev's Oracle IP (141.148.146.79) reaches Yahoo fine. Rather than hardcoding "Yahoo → oci-dev", we designed a state-driven system where declared worker nodes are probed for provider reachability and routing follows the result automatically. Also added per-caller attribution (media_rename, atlas, mcp, cli) for dashboard observability.

## What's Done

- [x] Diagnosed Yahoo 500 INKApi Error (Spectrum IP blocked, not headers fixable) (commit: various)
- [x] Fixed HTTP API missing `free_only` passthrough to SearchQuery (commit: 57377c5)
- [x] Fixed `SearchRequest` schema missing `free_only` field (commit: 57377c5)
- [x] Bumped homelab `POOL_TARGET_RESULTS` 25→50 in media_rename config (commit: 7885ad7 on homelab repo)
- [x] Verified argus `free_only` live on homelab (50 results, DDG + SearXNG)
- [x] Brainstormed and approved multi-egress architecture (Option A: argus worker mode)
- [x] Wrote spec: `docs/superpowers/specs/2026-05-22-multi-egress-worker-design.md` (commit: d10b0c7)
- [x] Updated spec with per-caller attribution section (commit: d10b0c7)
- [x] Wrote implementation plan: `docs/superpowers/plans/2026-05-22-multi-egress-worker.md` (commit: 5361110)

## In Progress

Nothing in progress — plan is complete and ready to execute.

## Not Started (10 tasks in plan)

- [ ] **Task 1**: EgressNode config + caller/egress on SearchQuery/ProviderTrace models
- [ ] **Task 2**: DB schema — caller + egress columns on provider_usage
- [ ] **Task 3**: Wire caller through all call sites (CLI, MCP, HTTP API, persistence)
- [ ] **Task 4**: ReachabilityMatrix core state logic (pure, fully testable)
- [ ] **Task 5**: Worker server — `argus worker` FastAPI app + CLI subcommand
- [ ] **Task 6**: RemoteProviderClient (BaseProvider → POST /exec)
- [ ] **Task 7**: Wire ReachabilityMatrix into ProviderExecutor
- [ ] **Task 8**: Startup probe — background task + probe_all logic
- [ ] **Task 9**: Dashboard caller breakdown + egress in health detail
- [ ] **Task 10**: Homelab deployment + vault setup (operational, not code)

## Active Files

Plan and spec are the primary references:
- `docs/superpowers/plans/2026-05-22-multi-egress-worker.md` — full TDD plan, 10 tasks
- `docs/superpowers/specs/2026-05-22-multi-egress-worker-design.md` — approved design spec

Key files the plan touches:
- `argus/config.py` — add `EgressNode` dataclass, `egress_nodes` field, remove `ResidentialConfig.endpoints`
- `argus/models.py` — `SearchQuery.caller`, `ProviderTrace.egress`
- `argus/broker/reachability.py` — NEW: `EgressProbe`, `ReachabilityMatrix`
- `argus/broker/remote_provider.py` — NEW: `RemoteProviderClient`
- `argus/worker/server.py` — NEW: FastAPI worker app
- `argus/broker/execution.py` — add reachability routing before health/budget checks
- `argus/broker/router.py` — wire ReachabilityMatrix into SearchBroker + create_broker
- `argus/cli/main.py` — add `worker` subcommand + `--caller` on search
- `argus/api/usage.py` — add `get_caller_activity()`
- `argus/persistence/models.py` — `ProviderUsageRow.caller` + `.egress`
- `argus/persistence/db.py` — add provider_usage to `_ensure_schema_compat`

## Key Decisions Made

1. **Option A (argus worker) over SSH on-demand** | Rationale: persistent HTTP over Tailscale has lower per-request latency than SSH cold-start; no daemon on primary means workers just run `argus worker` + systemd unit

2. **State-driven, not config-driven routing** | Rationale: user explicitly wanted "if Spectrum unblocks tomorrow, nothing changes" — routing table comes from live reachability probes, not hardcoded env vars

3. **Tier-0 probed eagerly, tier-1/3 never probed** | Rationale: probing paid providers burns credits; assume reachable, track reactively via health tracker on failures

4. **Local always preferred over workers** | Rationale: lower latency; only fall back to worker when local is confirmed blocked by probe

5. **`caller` is self-reported, no auth** | Rationale: internal observability only; no threat model justifies enforcement overhead

6. **`ResidentialConfig.endpoints` removed** | Rationale: superseded by `EgressNode`; keeping both would confuse future contributors

## Important Discoveries

- **homelab (Spectrum 23.241.236.110) is blocked by Yahoo** even though it's residential — Spectrum IP ranges are in Yahoo's blocklist. Oracle datacenter (141.148.146.79) passes through.
- **oci-dev gets 200 from Yahoo** despite being a datacenter IP — Oracle ASN is not in Yahoo's blocklist.
- **Gemini CLI syntax**: use `gemini --yolo -p "prompt"` for non-interactive mode. NOT `gemini --sandbox danger-full-access` (that's Codex CLI). NOT positional + `-p` together.
- **`ResidentialConfig.endpoints` already existed** but was never wired into routing — the original design intended multi-egress but never finished it.
- **`_ensure_schema_compat` pattern** already exists in `argus/persistence/db.py` for additive SQLite column migrations. New columns go there, not in Alembic.
- **SearXNG aggregates Yahoo internally** — Yahoo direct provider was always redundant when SearXNG is running. It was only justified for pip-only deploys without Docker.

## Blockers / Open Questions

| # | Question | Status |
|---|---------|--------|
| 1 | Where exactly is the FastAPI app lifespan defined? (Task 8 notes to `grep -rn "lifespan"`) | Implementer needs to check |
| 2 | Does oci-dev have `uv` and argus deps set up for `uv run argus worker`? May need `pip install argus-search` or `uv pip install -e .` | Check during Task 10 |
| 3 | macmini is unreachable via SSH currently (was during this session) | Not blocking — oci-dev alone is sufficient for Yahoo |

## Next Steps (Prioritized)

1. **Immediate**: `/restore` this handoff, then invoke `superpowers:subagent-driven-development` to execute the plan at `docs/superpowers/plans/2026-05-22-multi-egress-worker.md`
2. **Task order**: Execute Tasks 1→10 in order — each task's tests must pass before committing and moving on
3. **After Task 10**: Verify `GET /api/admin/health/detail` shows `yahoo: best_egress=oci-dev` on homelab
4. **Then**: Test `free_only=true` via homelab HTTP API and confirm Yahoo results appear in output

## Resume

```
/restore @thoughts/shared/handoffs/2026-05-22-multi-egress-worker-handoff.md
```
