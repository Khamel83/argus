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
