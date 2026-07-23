import json

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


def test_mcp_init_writes_codex_local_stdio_config(tmp_path, monkeypatch):
    from argus.cli import main as cli_main

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".codex").mkdir()

    result = CliRunner().invoke(
        cli_main.cli,
        ["mcp", "init", "--global", "--client", "codex"],
    )

    assert result.exit_code == 0, result.output
    config = (tmp_path / ".codex" / "config.toml").read_text()
    assert "[mcp_servers.argus]" in config
    assert 'args = ["mcp", "serve"]' in config
    assert "bearer_token_env_var" not in config


def test_mcp_init_replaces_existing_codex_section_with_args_array(tmp_path, monkeypatch):
    from argus.cli import main as cli_main

    monkeypatch.setenv("HOME", str(tmp_path))
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        '[model_aliases]\n'
        '"gpt-5.3-codex" = "gpt-5.4"\n'
        '\n'
        '[mcp_servers.argus]\n'
        'command = "/old/argus"\n'
        'args = ["mcp", "serve"]\n'
        '\n'
        '[mcp_servers.janus]\n'
        'command = "janus-mcp"\n'
    )

    result = CliRunner().invoke(
        cli_main.cli,
        ["mcp", "init", "--global", "--client", "codex"],
    )

    assert result.exit_code == 0, result.output
    config = (codex_dir / "config.toml").read_text()
    assert config.count('[mcp_servers.argus]') == 1
    assert config.count('args = ["mcp", "serve"]') == 1
    assert '\n["mcp", "serve"]' not in config
    assert '[mcp_servers.janus]' in config


def test_search_free_flag_sets_free_only_on_query(monkeypatch):
    from argus.cli import main as cli_main

    seen = {}

    def fake_create_broker():
        class FakeBroker:
            async def search(self, q, compute_attribution=False):
                seen["free_only"] = q.free_only
                from argus.models import SearchResponse, SearchMode
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
                from argus.models import SearchResponse, SearchMode
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
                from argus.models import SearchResponse, SearchMode
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
                from argus.models import SearchResponse, SearchMode
                return SearchResponse(query=q.query, mode=q.mode, results=[])
        return FakeBroker()

    monkeypatch.setattr("argus.broker.router.create_broker", fake_create_broker)

    result = CliRunner().invoke(
        cli_main.cli,
        ["search", "-q", "test"],
    )

    assert result.exit_code == 0, result.output
    assert seen.get("caller") == "cli"
