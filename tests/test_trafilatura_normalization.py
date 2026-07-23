"""Shared Trafilatura result normalization and consumer behavior."""

from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from argus.extraction.models import ExtractorName


class DocumentLike:
    """Sanitized stand-in for Trafilatura 2.x's Document result."""

    def __init__(self, **values):
        self._values = values

    def as_dict(self):
        return dict(self._values)


def test_normalizes_document_and_mapping_shapes():
    from argus.extraction.trafilatura_result import normalize_trafilatura_result

    expected_text = "Sanitized article content."
    document = DocumentLike(
        text=expected_text,
        title="Safe title",
        author="Safe author",
        date="2026-07-22",
    )
    mapping = MappingProxyType(document.as_dict())

    for raw_result in (document, mapping):
        normalized = normalize_trafilatura_result(raw_result)

        assert normalized is not None
        assert normalized.text == expected_text
        assert normalized.title == "Safe title"
        assert normalized.author == "Safe author"
        assert normalized.date == "2026-07-22"


def test_missing_optional_fields_are_normalized_to_safe_defaults():
    from argus.extraction.trafilatura_result import normalize_trafilatura_result

    normalized = normalize_trafilatura_result({"text": "Content.", "title": None})

    assert normalized is not None
    assert normalized.title == ""
    assert normalized.author == ""
    assert normalized.date is None


@pytest.mark.parametrize(
    "raw_result",
    [
        None,
        object(),
        {},
        {"text": None},
        {"text": 42},
        {"text": "   "},
        DocumentLike(text=object()),
    ],
)
def test_malformed_results_are_rejected(raw_result):
    from argus.extraction.trafilatura_result import normalize_trafilatura_result

    assert normalize_trafilatura_result(raw_result) is None


def test_document_serialization_failure_is_rejected():
    from argus.extraction.trafilatura_result import normalize_trafilatura_result

    class BrokenDocument:
        def as_dict(self):
            raise ValueError("sanitized malformed document")

    assert normalize_trafilatura_result(BrokenDocument()) is None


def test_untrusted_mapping_cannot_override_provenance():
    from argus.extraction.trafilatura_result import normalize_trafilatura_result

    normalized = normalize_trafilatura_result(
        {
            "text": "Safe content.",
            "egress": "residential",
            "machine": "untrusted-host",
            "source_type": "authenticated",
            "extractor": "paid_api",
        }
    )

    assert normalized is not None
    assert normalized.text == "Safe content."
    assert not hasattr(normalized, "egress")
    assert not hasattr(normalized, "machine")
    assert not hasattr(normalized, "source_type")
    assert not hasattr(normalized, "extractor")


def test_authentication_path_accepts_document_results():
    from argus.extraction.auth_extractor import _extract_from_html

    with patch(
        "trafilatura.bare_extraction",
        return_value=DocumentLike(text="Authenticated content."),
    ):
        assert _extract_from_html("<article>sanitized</article>") == "Authenticated content."


@pytest.mark.asyncio
async def test_residential_path_accepts_document_results():
    from argus.extraction.residential_service import _extract_trafilatura

    response = MagicMock()
    response.is_redirect = False
    response.url = "https://example.com/article"
    response.text = "<article>sanitized</article>"

    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("httpx.AsyncClient", return_value=client),
        patch(
            "trafilatura.bare_extraction",
            return_value=DocumentLike(
                text="Residential content.",
                title=None,
                author=None,
                date="2026-07-22",
            ),
        ),
    ):
        result = await _extract_trafilatura("https://example.com/article")

    assert result == {
        "url": "https://example.com/article",
        "title": "",
        "text": "Residential content.",
        "author": "",
        "date": "2026-07-22",
        "word_count": 2,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_name", "entrypoint_name", "lookup_name", "extractor"),
    [
        (
            "argus.extraction.archive_extractor",
            "extract_archive_is",
            "_search_existing",
            ExtractorName.ARCHIVE_IS,
        ),
        (
            "argus.extraction.wayback_extractor",
            "extract_wayback",
            "_check_availability",
            ExtractorName.WAYBACK,
        ),
    ],
)
async def test_archive_paths_accept_document_results(
    module_name,
    entrypoint_name,
    lookup_name,
    extractor,
):
    module = __import__(module_name, fromlist=[entrypoint_name])
    entrypoint = getattr(module, entrypoint_name)
    archived_url = "https://web.archive.org/web/20260722/https://example.com/article"
    response = MagicMock()
    response.text = "<article>sanitized</article>"
    response.raise_for_status = MagicMock()

    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    patches = [
        patch(f"{module_name}.{lookup_name}", new=AsyncMock(return_value=archived_url)),
        patch(f"{module_name}._rate_limit", new=AsyncMock()),
        patch(f"{module_name}.httpx.AsyncClient", return_value=client),
        patch("trafilatura.fetch_url", return_value=None),
        patch(
            "trafilatura.bare_extraction",
            return_value=DocumentLike(
                text="Archived content.",
                title="Archived title",
                author="Archived author",
                date="2026-07-22",
            ),
        ),
    ]
    if module_name.endswith("wayback_extractor"):
        patches.append(
            patch(f"{module_name}._fetch_archived", new=AsyncMock(return_value=response.text))
        )

    for active_patch in patches:
        active_patch.start()
    try:
        result = await entrypoint("https://example.com/article")
    finally:
        for active_patch in reversed(patches):
            active_patch.stop()

    assert result.error is None
    assert result.text == "Archived content."
    assert result.title == "Archived title"
    assert result.author == "Archived author"
    assert result.date == "2026-07-22"
    assert result.extractor == extractor


@pytest.mark.asyncio
async def test_mcp_recovery_path_accepts_document_results():
    from argus.mcp.tools import _try_archive_ph

    response = MagicMock()
    response.status_code = 200
    response.url = "https://archive.ph/sanitized"
    response.text = "<article>sanitized</article>"

    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    content = " ".join(["archived"] * 60) + "."

    with (
        patch("httpx.AsyncClient", return_value=client),
        patch(
            "trafilatura.bare_extraction",
            return_value=DocumentLike(text=content, title=None),
        ),
    ):
        result = await _try_archive_ph("https://example.com/article")

    assert result is not None
    assert result["title"] == ""
    assert result["snippet"] == content[:200]
    assert result["provider"] == "archive_ph"
