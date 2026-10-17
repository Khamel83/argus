"""Public AI-chat share links: detection, cleanup, and access checks.

Gemini, ChatGPT and Claude publish conversations as client-rendered share
pages.  Static extractors observe only site chrome ("Sign in", navigation and
footer), so a share URL is routed to the browser (Playwright) stage instead of
being accepted or rejected on the static result.  The renderer selects visible document content while preserving transcript
text, and a page that only resolves to a login wall is
reported as not public rather than returned as content.

This module is deliberately provider-shape free: it deals in URLs, hosts and
plain text only.  Provider adapters and their payload shapes stay in
``argus/providers``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

__all__ = [
    "SHARE_RENDER_SETTLE_SECONDS",
    "ShareLink",
    "normalize_share_link",
    "share_render_hosts",
    "share_access_failure",
    "normalize_share_text",
]

# Client-rendered conversations need a settle window after ``domcontentloaded``
# because the transcript is fetched and painted by the SPA.
SHARE_RENDER_SETTLE_SECONDS = 3.0

_GEMINI_CANONICAL = "https://gemini.google.com/share/{share_id}"
_GEMINI_HOSTS = ("share.gemini.google", "gemini.google.com")
_CHATGPT_HOSTS = ("chatgpt.com", "www.chatgpt.com", "chat.openai.com")
_CLAUDE_HOSTS = ("claude.ai", "www.claude.ai")

# Login and authentication paths.  Matched on the path alone so a login wall is
# recognised even when the redirect stays on a share host: the first segment is
# an auth endpoint (``/login``, ``/auth``) and everything nested beneath it is
# part of the same wall (``/auth/login``, ``/signin/v2/identifier``,
# ``/log-in/password``).  Share identifiers never match: they live one segment
# past ``/share/``.
_LOGIN_PATHS = re.compile(
    r"^/(?:login|log-in|signin|sign-in|auth|u/login)(?:/|$)",
    re.IGNORECASE,
)

# Redirect destinations that are always a login wall, never a public share.
_AUTH_HOSTS = frozenset(
    {
        "accounts.google.com",
        "auth.openai.com",
        "auth0.openai.com",
        "login.openai.com",
    }
)

# Edge chrome labels.  Navigation, brand and footer rows are matched whole-line
# so a conversation line that merely contains one of these words is preserved.
_CHROME_LABELS = frozenset(
    {
        "sign in",
        "sign in to continue",
        "sign up",
        "signup",
        "log in",
        "login",
        "new chat",
        "new conversation",
        "send message",
        "send a message",
        "ask anything",
        "share",
        "help",
        "settings",
        "menu",
        "terms of service",
        "terms",
        "privacy policy",
        "privacy",
        "cookie policy",
        "cookie settings",
        "cookie preferences",
        "cookies",
        "about",
        # Provider wordmarks as they appear in the share page shell.
        "gemini",
        "chatgpt",
        "claude",
    }
)

_STRIP_CHARS = " \t\u200b\u200c\u200d\u2060•·|/\\-–—"


@dataclass(frozen=True, slots=True)
class ShareLink:
    """A recognised public AI-chat share URL."""

    provider: str
    share_id: str
    canonical_url: str
    render_hosts: tuple[str, ...]


def _share_id(path: str, prefix: str) -> str:
    remainder = path[len(prefix) :] if path.startswith(prefix) else ""
    return remainder.split("/", 1)[0].strip()


def normalize_share_link(url: str) -> ShareLink | None:
    """Return the share identity for a supported URL, or ``None``.

    Recognises the Gemini share host and its canonical form, ChatGPT shares
    (including the legacy ``chat.openai.com`` host), and Claude shares.  An
    empty or pathless URL is not a share link.
    """

    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"

    if host == "share.gemini.google":
        share_id = _share_id(path, "/")
        if not share_id:
            return None
        return ShareLink(
            provider="gemini",
            share_id=share_id,
            canonical_url=_GEMINI_CANONICAL.format(share_id=share_id),
            render_hosts=_GEMINI_HOSTS,
        )
    if host == "gemini.google.com" and path.startswith("/share/"):
        share_id = _share_id(path, "/share/")
        if not share_id:
            return None
        return ShareLink(
            provider="gemini",
            share_id=share_id,
            canonical_url=_GEMINI_CANONICAL.format(share_id=share_id),
            render_hosts=_GEMINI_HOSTS,
        )
    if host in _CHATGPT_HOSTS and path.startswith("/share/"):
        share_id = _share_id(path, "/share/")
        if not share_id:
            return None
        return ShareLink(
            provider="chatgpt",
            share_id=share_id,
            canonical_url=f"https://chatgpt.com/share/{share_id}",
            render_hosts=_CHATGPT_HOSTS,
        )
    if host in _CLAUDE_HOSTS and path.startswith("/share/"):
        share_id = _share_id(path, "/share/")
        if not share_id:
            return None
        return ShareLink(
            provider="claude",
            share_id=share_id,
            canonical_url=f"https://claude.ai/share/{share_id}",
            render_hosts=_CLAUDE_HOSTS,
        )
    return None


def share_render_hosts(url: str) -> frozenset[str]:
    """Return the hosts a recognised share render may legitimately reach.

    Includes both sides of the ``share.gemini.google`` → ``gemini.google.com``
    redirect so the browser policy can admit that cross-domain hop.
    """

    link = normalize_share_link(url)
    return frozenset(link.render_hosts) if link is not None else frozenset()


def _is_chrome_line(line: str) -> bool:
    token = line.strip().strip(_STRIP_CHARS).strip().lower()
    return bool(token) and token in _CHROME_LABELS


def normalize_share_text(text: str) -> str:
    """Trim whitespace only; text labels do not establish shell ownership.

    DOM extraction selects the conversation container. A transcript answer
    such as "Privacy" must never disappear because it resembles navigation.
    """
    return (text or "").strip()


def share_access_failure(
    final_url: str, text: str, title: str = "", *, access_wall: bool = False
) -> str | None:
    """Return a stable not-public reason for a share render, or ``None``.

    A login redirect (by destination or by rendering only chrome/login chrome)
    is reported as not public; it is never projected as conversation content.
    """

    try:
        parsed = urlsplit(final_url or "")
    except ValueError:
        parsed = None
    host = (parsed.hostname or "").lower() if parsed else ""
    path = (parsed.path or "/") if parsed else "/"

    if host in _AUTH_HOSTS or _LOGIN_PATHS.match(path):
        return "share_link_not_public: login redirect"

    if re.match(r"^(?:sign[ -]?in|log[ -]?in)(?:\b|$)", title.strip(), re.I):
        return "share_link_not_public: login page title"
    cleaned = normalize_share_text(text)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines or all(_is_chrome_line(line) for line in lines):
        return "share_link_not_public: only site chrome rendered"
    if access_wall:
        return "share_link_not_public: rendered authentication form"
    return None
