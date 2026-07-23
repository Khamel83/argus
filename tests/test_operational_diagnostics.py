"""CLI and MCP presentation of authoritative nested status."""

from click.testing import CliRunner


class _Authority:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    async def request(self, method, path, *, token=None, payload=None):
        self.requests.append((method, path, token, payload))
        return self.responses[path]


def _provider_payload():
    return {
        "status": "degraded",
        "providers": {
            "duckduckgo": {
                "state": "healthy",
                "effective_status": "healthy",
                "observations": {
                    "reachability": {
                        "state": "healthy",
                        "reason": None,
                    }
                },
            },
            "brave": {
                "state": "unready",
                "effective_status": "temporarily_disabled_after_failures",
                "observations": {
                    "reachability": {
                        "state": "unready",
                        "reason": "probe_failed",
                    },
                    "cooldown": {
                        "state": "unready",
                        "reason": "cooldown_active",
                    },
                },
            },
        },
    }


def _budget_payload():
    return {
        "providers": {
            "duckduckgo": {
                "remaining": None,
                "argus_estimated_charge": 0,
                "uncertain_charge": 0,
            },
            "brave": {
                "remaining": 0,
                "argus_estimated_charge": 100,
                "uncertain_charge": 0,
            },
        }
    }


async def test_mcp_renders_nested_failures_and_unlimited_distinct_from_zero():
    from argus.mcp.http_adapter import HttpMcpAdapter

    authority = _Authority(
        {
            "/api/provider-health": _provider_payload(),
            "/api/budgets": _budget_payload(),
        }
    )
    adapter = HttpMcpAdapter(authority)

    health = await adapter.search_health()
    budgets = await adapter.search_budgets()

    assert "reachability=unready (probe_failed)" in health
    assert "cooldown=unready (cooldown_active)" in health
    assert "duckduckgo" in budgets and "remaining=unlimited" in budgets
    assert "brave" in budgets and "remaining=0" in budgets
    assert [path for _, path, _, _ in authority.requests] == [
        "/api/provider-health",
        "/api/budgets",
    ]


def test_cli_renders_nested_failures_and_unlimited_distinct_from_zero(monkeypatch):
    from argus.cli import main as cli_main

    authority = _Authority(
        {
            "/api/provider-health": _provider_payload(),
            "/api/budgets": _budget_payload(),
        }
    )
    monkeypatch.setattr(cli_main, "_http_authority_client", lambda: authority)

    health = CliRunner().invoke(cli_main.cli, ["health"])
    budgets = CliRunner().invoke(cli_main.cli, ["budgets"])

    assert health.exit_code == 0, health.output
    assert "reachability=unready (probe_failed)" in health.output
    assert "cooldown=unready (cooldown_active)" in health.output
    assert budgets.exit_code == 0, budgets.output
    assert "duckduckgo" in budgets.output and "remaining=unlimited" in budgets.output
    assert "brave" in budgets.output and "remaining=0" in budgets.output
    assert [path for _, path, _, _ in authority.requests] == [
        "/api/provider-health",
        "/api/budgets",
    ]
