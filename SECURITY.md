# Security Policy

## Supported Versions

Only the latest release is actively maintained. Check [PyPI](https://pypi.org/project/argus-search/) for the current version.

## Reporting a Vulnerability

Please report security issues privately — **do not open a public GitHub issue**.

Preferred: open a [private security advisory](https://github.com/Khamel83/argus/security/advisories/new)
on this repository. That keeps the report visible only to the maintainers until a
fix is ready.

If you cannot use GitHub Security Advisories, email the maintainer at
`security@khamel.com` with a description, reproduction steps, and any proposed
mitigation.

Expect an initial acknowledgement within a few days. We aim to resolve confirmed
vulnerabilities and publish a fix before disclosing details.

## What Argus Handles

- **SSRF protection**: All URL extraction blocks private/internal IP ranges (10.x, 172.16-31.x, 192.168.x, 127.x, ::1)
- **Domain rate limiting**: 10 requests/minute per domain to prevent abuse
- **Remote auth**: non-local HTTP callers and remote MCP transports must authenticate with `ARGUS_API_KEY`
- **Admin separation**: privileged HTTP routes live under `/api/admin/*` and require the admin key
- **Residential extraction worker auth**: residential extraction requires a shared bearer secret and caller allowlist.
- **Legacy egress worker auth (1.6.5+)**: `argus worker` requires explicit
  `ARGUS_ENV=development` and a nonempty `ARGUS_EGRESS_SHARED_SECRET`, even on
  loopback. Every `POST /exec` validates the bearer token before provider dispatch.
  This worker does not implement the residential extraction caller allowlist.
- **No user data storage**: Search queries and sessions are stored locally by the user — nothing is sent to Argus servers
- **API keys**: Keys are read from environment variables only — never logged, transmitted, or stored outside the user's config

## Deployment Defaults

- Legacy egress workers default to `127.0.0.1:8273`. To reach one from a trusted
  peer, explicitly set `ARGUS_WORKER_BIND=HOST:PORT` and use the shared bearer
  secret over a protected network. Production execution belongs to the HTTP authority.
- Prefer loopback binds (`127.0.0.1`) unless you are intentionally exposing Argus to trusted peers.
- If you publish Argus on a network, set `ARGUS_API_KEY` first and use `ARGUS_ADMIN_API_KEY` for privileged routes where possible.
- Treat remote MCP as an authenticated interface. Local `stdio` is the safest default.
- Treat residential extraction as an internal worker on Tailscale/private IP space, not a general-purpose HTTP fetch service.

## Dependencies

Argus relies on `httpx` for outbound HTTP requests. Keep dependencies updated:

```bash
pip install --upgrade argus-search
```

Dependabot is enabled on this repository for automated dependency tracking.
