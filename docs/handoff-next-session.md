# Handoff For Fresh Session

## Intent

Continue the Argus tooling and service hardening program from a clean context without re-discovering the core decisions.

## Locked Decisions

- Primary runtime: Python 3.12
- Safe default deployment: Tailscale-only internal service
- Internet exposure is secondary and must sit behind a reverse proxy
- Breaking changes are acceptable when justified by security or operability
- Scope includes repo tooling, CI, API/MCP auth, deployment assets, docs, and residential extraction

## Important Current Findings

- Repo declares `requires-python = ">=3.11"` in `pyproject.toml`
- Docker already uses Python 3.12
- Existing CI already runs tests on 3.11, 3.12, and 3.13
- Local shell in this environment is Python 3.9.4, so repo commands must run from the repo root with `uv`
- Local tooling is now standardized on `uv` plus Python 3.12, with `uv.lock` committed
- HTTP caller routes now require auth for non-local clients
- Privileged HTTP routes now live under `/api/admin/*`
- Remote MCP now requires bearer auth and exposes a reduced tool surface
- Residential extraction now requires a shared secret and caller allowlist
- Some docs/examples were updated, but final docs polish may still be needed if the route/tool contract changes again

## Files Reviewed Already

- `pyproject.toml`
- `.github/workflows/ci.yml`
- `argus/api/main.py`
- `argus/api/routes_admin.py`
- `argus/api/routes_extract.py`
- `argus/api/routes_health.py`
- `argus/api/rate_limit.py`
- `argus/extraction/ssrf.py`
- `argus/extraction/auth_extractor.py`
- `argus/extraction/cookies.py`
- `argus/extraction/residential_service.py`
- `argus/extraction/residential_extractor.py`
- `scripts/start-server.sh`
- `scripts/start-mcp.sh`
- `Dockerfile`
- `docker-compose.yml`
- `README.md`
- `.env.example`
- `SECURITY.md`

## Existing Dirty State

- `scripts/start-server.sh` already has user-side modifications in the working tree
- Do not overwrite or revert that file without reading and intentionally merging around the user changes

## First Recommended Actions

1. Re-read `README.md`, `.env.example`, and `SECURITY.md` for any remaining contract drift
2. Decide whether to keep or narrow the 3.11/3.13 CI matrix long-term
3. If needed, add more explicit deployment docs for reverse-proxy internet exposure
4. If needed, add dedicated MCP tests for remote-vs-local tool surface differences
5. If preparing a release, write a short changelog entry covering the new auth model

## Verification Notes

- Running from `~` will fail because `uv` loses project context; use the repo root or `uv --directory ...`
- Latest full verification succeeded with `uv run --python 3.12 --extra dev --extra mcp pytest tests/ -q`

## Repo Plan

- See `docs/security-hardening-plan.md` for the full work plan and acceptance criteria
