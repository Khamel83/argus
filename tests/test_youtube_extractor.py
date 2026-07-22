"""YouTube extraction behavior without live network calls."""

import asyncio


def test_extract_youtube_returns_metadata_and_manual_caption_provenance():
    from argus.extraction.models import ExtractorName
    from argus.extraction.youtube_extractor import extract_youtube

    async def load_info(_url):
        return {
            "id": "abc123DEF45",
            "webpage_url": "https://www.youtube.com/watch?v=abc123DEF45",
            "title": "Private fixture title",
            "uploader": "Private fixture channel",
            "upload_date": "20260721",
            "subtitles": {
                "en": [
                    {
                        "ext": "json3",
                        "url": "https://www.youtube.com/api/timedtext?fixture=manual",
                    }
                ]
            },
            "automatic_captions": {
                "en": [
                    {
                        "ext": "json3",
                        "url": "https://www.youtube.com/api/timedtext?fixture=auto",
                    }
                ]
            },
        }

    async def load_caption(url):
        assert url.endswith("fixture=manual")
        return {
            "events": [
                {"segs": [{"utf8": "first "}, {"utf8": "caption"}]},
                {"segs": [{"utf8": "second caption"}]},
            ]
        }

    result = asyncio.run(
        extract_youtube(
            "abc123DEF45", info_loader=load_info, caption_loader=load_caption
        )
    )

    assert result.url == "https://www.youtube.com/watch?v=abc123DEF45"
    assert result.title == "Private fixture title"
    assert result.author == "Private fixture channel"
    assert result.date == "2026-07-21"
    assert result.text == "first caption\nsecond caption"
    assert result.word_count == 4
    assert result.extractor == ExtractorName.YOUTUBE
    assert result.source_type == "live"
    assert result.cost == 0.0
    assert result.error is None


def test_generic_extract_routes_youtube_before_webpage_chain(monkeypatch):
    from argus.extraction import extractor
    from argus.extraction.models import ExtractedContent, ExtractorName

    expected = ExtractedContent(
        url="https://www.youtube.com/watch?v=abc123DEF45",
        title="fixture",
        text="fixture transcript with enough words",
        word_count=5,
        extractor=ExtractorName.YOUTUBE,
        source_type="live",
    )

    async def fake_youtube(_url):
        return expected

    async def generic_webpage_extractor(_url):
        raise AssertionError("generic webpage chain must not run for YouTube")

    monkeypatch.setattr(
        "argus.extraction.youtube_extractor.extract_youtube", fake_youtube
    )
    monkeypatch.setattr(extractor, "_extract_trafilatura", generic_webpage_extractor)
    extractor.get_extraction_cache().clear()

    result = asyncio.run(
        extractor.extract_url("https://www.youtube.com/watch?v=abc123DEF45")
    )

    assert result is expected
    assert result.extractors_tried == ["youtube"]


def test_extract_youtube_prefers_english_auto_caption_over_non_english_manual():
    from argus.extraction.youtube_extractor import extract_youtube

    async def load_info(_url):
        return {
            "id": "abc123DEF45",
            "subtitles": {
                "es": [{"ext": "json3", "url": "https://captions/manual-es"}]
            },
            "automatic_captions": {
                "en-US": [{"ext": "json3", "url": "https://captions/auto-en"}]
            },
        }

    async def load_caption(url):
        return {"events": [{"segs": [{"utf8": url.rsplit("/", 1)[-1]}]}]}

    result = asyncio.run(
        extract_youtube(
            "abc123DEF45", info_loader=load_info, caption_loader=load_caption
        )
    )

    assert result.text == "auto-en"
