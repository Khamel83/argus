"""Free YouTube metadata and caption extraction through local ``yt-dlp``.

This adapter deliberately uses no browser cookies and no paid API.  It accepts
either a canonical YouTube URL or an 11-character video ID and returns the
same ``ExtractedContent`` contract used by Argus' generic extraction chain.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from datetime import datetime
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

import httpx

from argus.extraction.models import ExtractedContent, ExtractorName

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def normalize_youtube_input(value: str) -> tuple[str, str] | None:
    """Return ``(canonical_url, video_id)`` for a supported URL or bare ID."""
    candidate = value.strip()
    if _VIDEO_ID_RE.fullmatch(candidate):
        return f"https://www.youtube.com/watch?v={candidate}", candidate

    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    video_id = ""
    if host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
            video_id = parsed.path.split("/", 2)[2].split("/", 1)[0]
    elif host == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/", 1)[0]

    if not _VIDEO_ID_RE.fullmatch(video_id):
        return None
    return f"https://www.youtube.com/watch?v={video_id}", video_id


async def _load_info(url: str) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        from yt_dlp import YoutubeDL

        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 20,
        }
        with YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=False)

    return await asyncio.to_thread(run)


async def _load_caption(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "Argus/1.0"})
        response.raise_for_status()
        return response.json()


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _choose_caption(info: dict[str, Any]) -> str | None:
    """Prefer manual English JSON captions, then automatic English captions."""
    collections = ("subtitles", "automatic_captions")
    for english_only in (True, False):
        for collection_name in collections:
            collection = info.get(collection_name) or {}
            selected = {
                language: formats
                for language, formats in collection.items()
                if language.lower().startswith("en") == english_only
            }
            caption_url = _first_json3_caption(selected)
            if caption_url:
                return caption_url
    return None


def _first_json3_caption(collection: dict[str, Any]) -> str | None:
    for language in sorted(collection):
        formats = collection.get(language) or []
        preferred = sorted(formats, key=lambda item: item.get("ext") != "json3")
        for item in preferred:
            if item.get("ext") == "json3" and item.get("url"):
                return str(item["url"])
    return None


def _json3_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for event in payload.get("events", []):
        text = "".join(segment.get("utf8", "") for segment in event.get("segs", []))
        text = " ".join(text.replace("\n", " ").split())
        if text and (not lines or lines[-1] != text):
            lines.append(text)
    return "\n".join(lines)


def _upload_date(value: Any) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y%m%d").date().isoformat()
    except ValueError:
        return None


async def extract_youtube(
    value: str,
    *,
    info_loader: Callable[[str], Awaitable[dict[str, Any]] | dict[str, Any]]
    | None = None,
    caption_loader: Callable[[str], Awaitable[dict[str, Any]] | dict[str, Any]]
    | None = None,
) -> ExtractedContent:
    """Extract public metadata and available captions for one YouTube video."""
    normalized = normalize_youtube_input(value)
    if normalized is None:
        return ExtractedContent(url=value, error="youtube: invalid video URL or ID")
    canonical_url, video_id = normalized
    info_loader = info_loader or _load_info
    caption_loader = caption_loader or _load_caption

    try:
        info = await _resolve(info_loader(canonical_url))
    except Exception as exc:
        return ExtractedContent(
            url=canonical_url,
            error=f"youtube: metadata unavailable ({type(exc).__name__})",
            extractor=ExtractorName.YOUTUBE,
            source_type="live",
            egress="local",
        )

    resolved_id = str(info.get("id") or video_id)
    if not _VIDEO_ID_RE.fullmatch(resolved_id):
        resolved_id = video_id
    resolved_url = f"https://www.youtube.com/watch?v={resolved_id}"
    text = ""
    caption_url = _choose_caption(info)
    if caption_url:
        try:
            text = _json3_text(await _resolve(caption_loader(caption_url)))
        except Exception:
            # Metadata-only extraction is still useful and safely retryable by callers.
            text = ""

    return ExtractedContent(
        url=resolved_url,
        title=str(info.get("title") or ""),
        text=text,
        author=str(info.get("uploader") or info.get("channel") or ""),
        date=_upload_date(info.get("upload_date")),
        word_count=len(text.split()),
        extractor=ExtractorName.YOUTUBE,
        quality_passed=True,
        quality_reason=None if text else "captions_unavailable",
        extractors_tried=["youtube"],
        source_type="live",
        egress="local",
        cost=0.0,
    )
