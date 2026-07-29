import json

import click
import httpx
from click.testing import CliRunner


def test_cli_version_reports_argus_package_version():
    from argus import __version__
    from argus.cli import main as cli_main

    result = CliRunner().invoke(cli_main.cli, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"argus, version {__version__}"


def test_extract_cli_passes_archive_ingest_mode(monkeypatch):
    from argus.cli import main as cli_main
    from argus.extraction.models import ExtractedContent, ExtractorName

    seen = {}

    async def fake_extract_url(url, domain=None, mode="default", *, caller=""):
        seen["url"] = url
        seen["domain"] = domain
        seen["mode"] = mode
        seen["caller"] = caller
        return ExtractedContent(
            url=url,
            title="Example",
            text="content",
            word_count=1,
            extractor=ExtractorName.TRAFILATURA,
        )

    monkeypatch.setattr("argus.extraction.extract_url", fake_extract_url)

    result = CliRunner().invoke(
        cli_main.cli,
        ["extract", "-u", "https://example.com", "--mode", "archive_ingest", "--json"],
    )

    assert result.exit_code == 0
    assert seen == {
        "url": "https://example.com",
        "domain": None,
        "mode": "archive_ingest",
        "caller": "cli",
    }
    assert '"mode": "archive_ingest"' in result.output


def test_mcp_init_writes_opencode_native_local_config(tmp_path, monkeypatch):
    from argus.cli import main as cli_main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_MCP_STANDALONE", "true")
    (tmp_path / ".config" / "opencode").mkdir(parents=True)

    result = CliRunner().invoke(
        cli_main.cli,
        ["mcp", "init", "--global", "--client", "opencode"],
    )

    assert result.exit_code == 0, result.output
    config = json.loads((tmp_path / ".config" / "opencode" / "config.json").read_text())
    argus = config["mcp"]["argus"]
    assert argus["type"] == "local"
    assert argus["command"][-2:] == ["mcp", "serve"]
    assert argus["enabled"] is True
    assert argus["environment"] == {"ARGUS_MCP_STANDALONE": "true"}


def test_mcp_init_writes_codex_local_stdio_config(tmp_path, monkeypatch):
    from argus.cli import main as cli_main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_MCP_STANDALONE", "true")
    (tmp_path / ".codex").mkdir()

    result = CliRunner().invoke(
        cli_main.cli,
        ["mcp", "init", "--global", "--client", "codex"],
    )

    assert result.exit_code == 0, result.output
    config = (tmp_path / ".codex" / "config.toml").read_text()
    assert "[mcp_servers.argus]" in config
    assert 'args = ["mcp", "serve"]' in config
    assert 'env = { ARGUS_MCP_STANDALONE = "true" }' in config
    assert "bearer_token_env_var" not in config


def test_mcp_init_replaces_existing_codex_section_with_args_array(
    tmp_path, monkeypatch
):
    from argus.cli import main as cli_main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_MCP_STANDALONE", "true")
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        "[model_aliases]\n"
        '"gpt-5.3-codex" = "gpt-5.4"\n'
        "\n"
        "[mcp_servers.argus]\n"
        'command = "/old/argus"\n'
        'args = ["mcp", "serve"]\n'
        "\n"
        "[mcp_servers.janus]\n"
        'command = "janus-mcp"\n'
    )

    result = CliRunner().invoke(
        cli_main.cli,
        ["mcp", "init", "--global", "--client", "codex"],
    )

    assert result.exit_code == 0, result.output
    config = (codex_dir / "config.toml").read_text()
    assert config.count("[mcp_servers.argus]") == 1
    assert config.count('args = ["mcp", "serve"]') == 1
    assert '\n["mcp", "serve"]' not in config
    assert "[mcp_servers.janus]" in config


def test_mcp_init_rejects_unconfigured_local_adapter(tmp_path, monkeypatch):
    from argus.cli import main as cli_main

    monkeypatch.setenv("HOME", str(tmp_path))

    result = CliRunner().invoke(
        cli_main.cli,
        ["mcp", "init", "--client", "claude"],
    )

    assert result.exit_code != 0
    assert "ARGUS_AUTHORITY_URL" in result.output
    assert not (tmp_path / ".mcp.json").exists()


def test_search_free_flag_sets_free_only_on_query(monkeypatch):
    from argus.cli import main as cli_main

    seen = {}

    def fake_create_broker():
        class FakeBroker:
            async def search(self, q, compute_attribution=False):
                seen["free_only"] = q.free_only
                from argus.models import SearchResponse

                return SearchResponse(query=q.query, mode=q.mode, results=[])

        return FakeBroker()

    monkeypatch.setattr("argus.broker.router.create_broker", fake_create_broker)

    result = CliRunner().invoke(
        cli_main.cli,
        ["search", "-q", "hello world", "--free"],
    )

    assert result.exit_code == 0, result.output
    assert seen.get("free_only") is True


def test_search_without_free_flag_leaves_free_only_false(monkeypatch):
    from argus.cli import main as cli_main

    seen = {}

    def fake_create_broker():
        class FakeBroker:
            async def search(self, q, compute_attribution=False):
                seen["free_only"] = q.free_only
                from argus.models import SearchResponse

                return SearchResponse(query=q.query, mode=q.mode, results=[])

        return FakeBroker()

    monkeypatch.setattr("argus.broker.router.create_broker", fake_create_broker)

    result = CliRunner().invoke(
        cli_main.cli,
        ["search", "-q", "hello world"],
    )

    assert result.exit_code == 0, result.output
    assert seen.get("free_only") is False


def test_search_caller_flag_sets_caller_on_query(monkeypatch):
    from argus.cli import main as cli_main

    seen = {}

    def fake_create_broker():
        class FakeBroker:
            async def search(self, q, compute_attribution=False):
                seen["caller"] = q.caller
                from argus.models import SearchResponse

                return SearchResponse(query=q.query, mode=q.mode, results=[])

        return FakeBroker()

    monkeypatch.setattr("argus.broker.router.create_broker", fake_create_broker)

    result = CliRunner().invoke(
        cli_main.cli,
        ["search", "-q", "test", "--caller", "my_project"],
    )

    assert result.exit_code == 0, result.output
    assert seen.get("caller") == "my_project"


def test_search_caller_defaults_to_cli(monkeypatch):
    from argus.cli import main as cli_main

    seen = {}

    def fake_create_broker():
        class FakeBroker:
            async def search(self, q, compute_attribution=False):
                seen["caller"] = q.caller
                from argus.models import SearchResponse

                return SearchResponse(query=q.query, mode=q.mode, results=[])

        return FakeBroker()

    monkeypatch.setattr("argus.broker.router.create_broker", fake_create_broker)

    result = CliRunner().invoke(
        cli_main.cli,
        ["search", "-q", "test"],
    )

    assert result.exit_code == 0, result.output
    assert seen.get("caller") == "cli"


def test_provider_smoke_cli_marks_query_operational_only(monkeypatch):
    from argus.cli import main as cli_main
    from argus.models import ProviderName
    from unittest.mock import MagicMock

    seen = {}

    def fake_create_broker():
        class FakeBroker:
            readiness_service = MagicMock()
            readiness_service.authorize_probe.return_value.allowed = True

            def provider_readiness_projection(self, provider):
                seen["provider"] = provider
                return {"state": "healthy"}

        return FakeBroker()

    monkeypatch.setattr("argus.broker.router.create_broker", fake_create_broker)

    result = CliRunner().invoke(
        cli_main.cli,
        ["test-provider", "--provider", "duckduckgo"],
    )

    assert result.exit_code == 0, result.output
    assert seen["provider"] == ProviderName.DUCKDUCKGO
    assert "Fixture: verified" in result.output


def test_standalone_recover_url_dispatches_exactly(monkeypatch):
    from argus import standalone_cli
    from argus.cli import main as cli_main

    calls = []

    def fake_recover_url(**kwargs):
        calls.append(kwargs)
        click.echo("standalone recovery")

    monkeypatch.setattr(cli_main, "_http_authority_client", lambda: None)
    monkeypatch.setattr(standalone_cli, "recover_url", fake_recover_url)

    result = CliRunner().invoke(
        cli_main.cli,
        [
            "recover-url",
            "--url",
            "https://example.com/gone",
            "--title",
            "Lost",
            "--domain",
            "example.com",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == "standalone recovery\n"
    assert result.stderr == ""
    assert calls == [
        {
            "url": "https://example.com/gone",
            "title": "Lost",
            "domain": "example.com",
            "as_json": True,
        }
    ]


def test_standalone_balance_commands_dispatch_exactly(monkeypatch):
    from argus import standalone_cli
    from argus.cli import main as cli_main

    calls = []

    def fake_gate():
        calls.append(("gate",))

    def fake_check():
        calls.append(("check",))
        click.echo("checked balances")

    def fake_set_balance(**kwargs):
        calls.append(("set", kwargs))
        click.echo("set balance")

    monkeypatch.setenv("ARGUS_ENV", "development")
    monkeypatch.setattr(standalone_cli, "require_nonproduction", fake_gate)
    monkeypatch.setattr(standalone_cli, "check_balances", fake_check)
    monkeypatch.setattr(standalone_cli, "set_balance", fake_set_balance)

    check = CliRunner().invoke(cli_main.cli, ["check-balances"])
    update = CliRunner().invoke(
        cli_main.cli,
        ["set-balance", "--service", "jina", "--balance", "1250"],
    )

    assert check.exit_code == 0
    assert check.stdout == "checked balances\n"
    assert check.stderr == ""
    assert update.exit_code == 0
    assert update.stdout == "set balance\n"
    assert update.stderr == ""
    assert calls == [
        ("gate",),
        ("check",),
        ("gate",),
        ("set", {"service": "jina", "balance": 1250.0}),
    ]


def test_standalone_balance_command_is_gated_in_production(monkeypatch):
    from argus import standalone_cli
    from argus.cli import main as cli_main

    dispatched = False

    def forbidden_dispatch():
        nonlocal dispatched
        dispatched = True

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setattr(standalone_cli, "check_balances", forbidden_dispatch)

    result = CliRunner().invoke(cli_main.cli, ["check-balances"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Error: Standalone CLI execution is unavailable in production\n"
    )
    assert dispatched is False


def test_standalone_diagnostics_dispatch_exactly(monkeypatch):
    from argus import standalone_cli
    from argus.cli import main as cli_main

    calls = []

    def fake_gate():
        calls.append(("gate",))

    def fake_health():
        calls.append(("health",))
        click.echo("standalone health")

    def fake_budgets():
        calls.append(("budgets",))
        click.echo("standalone budgets")

    def fake_doctor(*, as_json):
        calls.append(("doctor", {"as_json": as_json}))
        click.echo("standalone doctor")

    monkeypatch.setenv("ARGUS_ENV", "development")
    monkeypatch.setattr(cli_main, "_http_authority_client", lambda: None)
    monkeypatch.setattr(standalone_cli, "require_nonproduction", fake_gate)
    monkeypatch.setattr(standalone_cli, "health", fake_health, raising=False)
    monkeypatch.setattr(standalone_cli, "budgets", fake_budgets, raising=False)
    monkeypatch.setattr(standalone_cli, "doctor", fake_doctor, raising=False)

    health = CliRunner().invoke(cli_main.cli, ["health"])
    budgets = CliRunner().invoke(cli_main.cli, ["budgets"])
    doctor = CliRunner().invoke(cli_main.cli, ["doctor", "--json"])

    assert health.exit_code == 0
    assert health.stdout == "standalone health\n"
    assert health.stderr == ""
    assert budgets.exit_code == 0
    assert budgets.stdout == "standalone budgets\n"
    assert budgets.stderr == ""
    assert doctor.exit_code == 0
    assert doctor.stdout == "standalone doctor\n"
    assert doctor.stderr == ""
    assert calls == [
        ("gate",),
        ("health",),
        ("gate",),
        ("budgets",),
        ("gate",),
        ("doctor", {"as_json": True}),
    ]


def test_configured_authority_diagnostics_take_precedence_over_standalone(
    monkeypatch,
):
    import argus.authority as authority_module

    from argus import standalone_cli
    from argus.cli import main as cli_main

    calls = []
    constructed = []

    def handler(request):
        calls.append(
            (
                request.method,
                request.url.path,
                request.headers.get("Authorization") == "Bearer configured-token",
            )
        )
        if request.url.path == "/api/provider-health":
            return httpx.Response(
                200,
                json={
                    "providers": {
                        "duckduckgo": {
                            "state": "healthy",
                            "observations": {},
                        }
                    }
                },
            )
        if request.url.path == "/api/budgets":
            return httpx.Response(
                200,
                json={
                    "providers": {
                        "duckduckgo": {
                            "remaining": None,
                            "argus_estimated_charge": 0,
                            "uncertain_charge": 0,
                        }
                    }
                },
            )
        if request.url.path == "/api/admin/status":
            return httpx.Response(
                200,
                json={"status": "ok", "build": {}, "providers": {}},
            )
        raise AssertionError(request.url.path)

    def forbidden_standalone(*_args, **_kwargs):
        raise AssertionError("configured authority must take precedence")

    for name in tuple(__import__("os").environ):
        if name.startswith("ARGUS_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARGUS_AUTHORITY_URL", "https://authority.example")
    monkeypatch.setenv("ARGUS_AUTHORITY_TOKEN", "configured-token")
    monkeypatch.setenv("ARGUS_ENV", "development")
    client_type = authority_module.HttpAuthorityClient

    def build_authority(config):
        constructed.append(config)
        return client_type(
            config,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(
        authority_module,
        "HttpAuthorityClient",
        build_authority,
    )
    monkeypatch.setattr(
        standalone_cli,
        "require_nonproduction",
        forbidden_standalone,
    )
    monkeypatch.setattr(
        standalone_cli,
        "health",
        forbidden_standalone,
        raising=False,
    )
    monkeypatch.setattr(
        standalone_cli,
        "budgets",
        forbidden_standalone,
        raising=False,
    )
    monkeypatch.setattr(
        standalone_cli,
        "doctor",
        forbidden_standalone,
        raising=False,
    )

    health = CliRunner().invoke(cli_main.cli, ["health"])
    budgets = CliRunner().invoke(cli_main.cli, ["budgets"])
    doctor = CliRunner().invoke(cli_main.cli, ["doctor", "--json"])

    assert health.exit_code == 0
    assert health.stdout == "  duckduckgo   HEALTHY\n"
    assert budgets.exit_code == 0
    assert budgets.stdout == (
        "Provider budgets:\n"
        "  duckduckgo   remaining=unlimited estimated=0 uncertain=0\n"
    )
    assert doctor.exit_code == 0
    assert json.loads(doctor.stdout) == {
        "status": "ok",
        "build": {},
        "providers": {},
    }
    assert (
        constructed
        == [
            authority_module.AuthorityClientConfig(
                "https://authority.example",
                "configured-token",
            )
        ]
        * 3
    )
    assert calls == [
        ("GET", "/api/provider-health", True),
        ("GET", "/api/budgets", True),
        ("GET", "/api/admin/status", True),
    ]


def test_standalone_corpus_import_dispatches_exactly(tmp_path, monkeypatch):
    from argus import standalone_cli
    from argus.cli import main as cli_main

    calls = []
    source = tmp_path / "legacy-docs"
    source.mkdir()

    def fake_gate():
        calls.append(("gate",))

    def fake_import(**kwargs):
        calls.append(("import", kwargs))
        click.echo("imported corpus")

    monkeypatch.setenv("ARGUS_ENV", "development")
    monkeypatch.setattr(standalone_cli, "require_nonproduction", fake_gate)
    monkeypatch.setattr(standalone_cli, "import_docs_cache", fake_import)

    result = CliRunner().invoke(
        cli_main.cli,
        ["corpus", "import-docs-cache", "--source", str(source), "--json"],
    )

    assert result.exit_code == 0
    assert result.stdout == "imported corpus\n"
    assert result.stderr == ""
    assert calls == [
        ("gate",),
        ("import", {"source": str(source), "as_json": True}),
    ]


def test_standalone_cookie_commands_dispatch_exactly(tmp_path, monkeypatch):
    from argus import standalone_cli
    from argus.cli import main as cli_main

    calls = []
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text("[]", encoding="utf-8")

    def fake_gate():
        calls.append(("gate",))

    def fake_import(**kwargs):
        calls.append(("import", kwargs))
        click.echo("imported cookies")

    def fake_health():
        calls.append(("health",))
        click.echo("cookie health")

    monkeypatch.setenv("ARGUS_ENV", "development")
    monkeypatch.setattr(standalone_cli, "require_nonproduction", fake_gate)
    monkeypatch.setattr(standalone_cli, "cookies_import", fake_import)
    monkeypatch.setattr(standalone_cli, "cookies_health", fake_health)

    imported = CliRunner().invoke(
        cli_main.cli,
        [
            "cookies",
            "import",
            "--domain",
            "example.com",
            "--file",
            str(cookie_file),
        ],
    )
    health = CliRunner().invoke(cli_main.cli, ["cookies", "health"])

    assert imported.exit_code == 0
    assert imported.stdout == "imported cookies\n"
    assert imported.stderr == ""
    assert health.exit_code == 0
    assert health.stdout == "cookie health\n"
    assert health.stderr == ""
    assert calls == [
        ("gate",),
        (
            "import",
            {"domain": "example.com", "filepath": str(cookie_file)},
        ),
        ("gate",),
        ("health",),
    ]


def test_mcp_serve_dispatches_to_explicit_standalone_launcher(monkeypatch):
    from argus import standalone_cli
    from argus.cli import main as cli_main

    calls = []

    def fake_serve_mcp(**kwargs):
        calls.append(kwargs)
        click.echo("standalone mcp")

    monkeypatch.setenv("ARGUS_ENV", "development")
    monkeypatch.setenv("ARGUS_MCP_STANDALONE", "true")
    monkeypatch.delenv("ARGUS_AUTHORITY_URL", raising=False)
    monkeypatch.setattr(standalone_cli, "serve_mcp", fake_serve_mcp)

    result = CliRunner().invoke(
        cli_main.cli,
        [
            "mcp",
            "serve",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.2",
            "--port",
            "9001",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == "standalone mcp\n"
    assert result.stderr == ""
    assert calls == [
        {
            "transport": "streamable-http",
            "host": "127.0.0.2",
            "port": 9001,
        }
    ]


def test_mcp_serve_standalone_launcher_is_gated_in_production(monkeypatch):
    from argus import standalone_cli
    from argus.cli import main as cli_main

    calls = []

    def forbidden_standalone(**kwargs):
        calls.append(("standalone", kwargs))

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_MCP_STANDALONE", "true")
    monkeypatch.delenv("ARGUS_AUTHORITY_URL", raising=False)
    monkeypatch.delenv("ARGUS_AUTHORITY_TOKEN", raising=False)
    monkeypatch.setenv("ARGUS_AUTOLOAD_DOTENV", "false")
    monkeypatch.setattr(standalone_cli, "serve_mcp", forbidden_standalone)

    result = CliRunner().invoke(cli_main.cli, ["mcp", "serve"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == ""
    assert str(result.exception) == (
        "MCP requires ARGUS_AUTHORITY_URL and authority authentication; "
        "standalone development must use the external development MCP launcher"
    )
    assert calls == []
