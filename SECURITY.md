# Security Policy

## Supported Versions

Only the latest release is actively maintained. Check [PyPI](https://pypi.org/project/argus-search/) for the current version.

## Reporting a Vulnerability

If you find a security vulnerability, please open a [GitHub issue](https://github.com/Khamel83/argus/issues/new?template=bug_report.md) with the `security` label.

## What Argus Handles

- **SSRF protection**: All URL extraction blocks private/internal IP ranges (10.x, 172.16-31.x, 192.168.x, 127.x, ::1)
- **Domain rate limiting**: 10 requests/minute per domain to prevent abuse
- **Remote auth**: non-local HTTP callers and remote MCP transports must authenticate with `ARGUS_API_KEY`
- **Admin separation**: privileged HTTP routes live under `/api/admin/*` and require the admin key
- **Residential worker auth**: residential extraction requires a shared bearer secret and caller allowlist
- **No user data storage**: Search queries and sessions are stored locally by the user — nothing is sent to Argus servers
- **API keys**: Keys are read from environment variables only — never logged, transmitted, or stored outside the user's config

## Deployment Defaults

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
