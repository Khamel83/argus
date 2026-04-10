# Security Policy

## Supported Versions

Only the latest release is actively maintained. Check [PyPI](https://pypi.org/project/argus-search/) for the current version.

## Reporting a Vulnerability

If you find a security vulnerability, please open a [GitHub issue](https://github.com/Khamel83/argus/issues/new?template=bug_report.md) with the `security` label.

## What Argus Handles

- **SSRF protection**: All URL extraction blocks private/internal IP ranges (10.x, 172.16-31.x, 192.168.x, 127.x, ::1)
- **Domain rate limiting**: 10 requests/minute per domain to prevent abuse
- **No user data storage**: Search queries and sessions are stored locally by the user — nothing is sent to Argus servers
- **API keys**: Keys are read from environment variables only — never logged, transmitted, or stored outside the user's config

## Dependencies

Argus relies on `httpx` for outbound HTTP requests. Keep dependencies updated:

```bash
pip install --upgrade argus-search
```

Dependabot is enabled on this repository for automated dependency tracking.
