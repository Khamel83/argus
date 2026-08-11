# MCP Stateless Compatibility and Clio Retirement

## Goal

Record the MCP 2026-07-28 protocol changes without claiming unsupported runtime
behavior, and remove the retired Clio caller from active Argus policy and
operator-facing configuration.

## Scope

- Keep the deployed MCP endpoint on its verified 2025-compatible transport
  contract until a separately tested 2026-07-28 migration is approved.
- Add the official MCP release and Simon Willison's operational analysis to a
  dated research note, with direct links and explicit adoption gates.
- Make current Argus documentation describe remote HTTPS MCP as the canonical
  production path; local stdio remains an explicit development option.
- Remove `clio*` from active caller-cap examples, parser comments, tests, and
  current context text. Preserve dated ADRs and historical plans as archival
  records.
- Update the Homelab environment generator so stale protected input cannot
  reintroduce the retired `clio*` cap. Keep the required active caps for
  `hermes`, `mac-agents`, and `maya`.
- Remove the stale local Codex project trust entry for the retired Clio
  checkout, after making a narrow backup.

## Non-goals

- Do not implement or advertise MCP 2026-07-28 support in this change.
- Do not rotate credentials, change provider budgets, or alter the deployed
  Argus image.
- Do not rewrite historical ADRs, dated plans, or migration evidence merely to
  erase the name Clio.

## Verification

- Argus unit tests for caller-cap parsing and attribution use only active
  caller examples.
- Homelab generator tests prove a stale `clio*` input is omitted and required
  active caps remain present.
- Documentation and research-note links are checked by diff and targeted
  grep.
- The generated production environment is inspected for absence of `clio*`
  before any Argus/MCP recreation; image digest and health remain unchanged.
