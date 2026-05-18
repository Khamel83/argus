import json

from click.testing import CliRunner


def test_extract_cli_passes_archive_ingest_mode(monkeypatch):
    from argus.cli import main as cli_main
    from argus.extraction.models import ExtractedContent, ExtractorName

    seen = {}

    async def fake_extract_url(url, domain=None, mode="default"):
        seen["url"] = url
        seen["domain"] = domain
        seen["mode"] = mode
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
