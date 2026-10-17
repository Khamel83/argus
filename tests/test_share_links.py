"""Public AI-chat share-link extraction: detection, cleanup, and canaries.

Hermetic only: no live browser, no network, no real provider.  Synthetic
rendered fixtures stand in for the client-rendered share pages.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from argus.acquisition.browser_policy import (
    BrowserAdmission,
    BrowserNetworkAttestation,
    current_release_identity,
    guard_browser_route,
    make_browser_request,
    require_browser_policy,
    set_browser_attestation_provider,
)
from argus.acquisition.models import OriginProfile
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.extraction.share_links import (
    ShareLink,
    normalize_share_link,
    share_access_failure,
    share_render_hosts,
    normalize_share_text,
)

_GEMINI_SHARE = "https://share.gemini.google/bQBdWIDX3xBr"
_CHATGPT_SHARE = "https://chatgpt.com/share/2f4c0a1e-0000"
_CLAUDE_SHARE = "https://claude.ai/share/9c1f0000"

# Synthetic rendered pages.  The transcript lines are edge-bounded by the site
# chrome that a static extractor would otherwise return as the whole document.
_GEMINI_RENDER = "\n".join(
    [
        "Sign in",
        "Gemini",
        "New chat",
        "Settings",
        "Help",
        "Terms of Service",
        "Privacy Policy",
        "",
        "You",
        "Explain reciprocal rank fusion.",
        "Gemini",
        "Reciprocal rank fusion combines several ranked lists into one ranking by "
        "summing one over a damping constant plus the rank of each document in "
        "every list that returned it. Documents that a list omits contribute "
        "nothing, so agreement across lists raises a document in the fused order.",
        "You",
        "Give a two line summary.",
        "Gemini",
        "It merges multiple rankings into a single score per document, and higher "
        "agreement across lists moves a document up while the damping constant "
        "keeps any single ranking from dominating.",
        "",
        "Sign in",
        "Cookie settings",
    ]
)

_CHATGPT_RENDER = "\n".join(
    [
        "Log in",
        "ChatGPT",
        "New conversation",
        "Share",
        "About",
        "Terms",
        "Privacy",
        "",
        "User",
        "What is a circuit breaker in a distributed system?",
        "ChatGPT",
        "A circuit breaker stops calling a failing dependency after a threshold of "
        "errors, then periodically probes it. While open it fails fast, which "
        "protects the caller and lets the dependency recover without a retry storm.",
        "User",
        "When does it close again?",
        "ChatGPT",
        "It moves to a half-open state after a cool-down and closes once the probe "
        "requests succeed, or reopens if they keep failing.",
        "",
        "Log in",
        "Cookie preferences",
    ]
)

_CLAUDE_RENDER = "\n".join(
    [
        "Claude",
        "Sign in",
        "Help",
        "Privacy policy",
        "",
        "Human",
        "Summarise the CAP theorem.",
        "Claude",
        "A distributed store cannot simultaneously guarantee consistency, "
        "availability, and partition tolerance; during a partition it must choose "
        "between consistency and availability.",
        "",
        "Sign in",
    ]
)

_CANARIES = [
    (_GEMINI_SHARE, "https://gemini.google.com/share/bQBdWIDX3xBr", _GEMINI_RENDER, "reciprocal rank fusion"),
    (_CHATGPT_SHARE, _CHATGPT_SHARE, _CHATGPT_RENDER, "circuit breaker"),
    (_CLAUDE_SHARE, _CLAUDE_SHARE, _CLAUDE_RENDER, "CAP theorem"),
]


@pytest.fixture(autouse=True)
def reset_browser_provider():
    set_browser_attestation_provider(None)
    yield
    set_browser_attestation_provider(None)


def _attestation(**overrides) -> BrowserNetworkAttestation:
    values = {
        "policy_identity": "proxy-policy-v1",
        "resolver_identity": "resolver-v1",
        "connection_binding": "connection-binding-v1",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "release_identity": current_release_identity(),
    }
    values.update(overrides)
    return BrowserNetworkAttestation(**values)


class _Provider:
    def __init__(self, attestation):
        self._attestation = attestation

    def current(self):
        return self._attestation


def _share_request(url: str):
    return make_browser_request(
        url,
        profile=OriginProfile.PUBLIC_CONTENT,
        request_id="share-test",
    )


@pytest.mark.parametrize(
    ("url", "provider", "share_id"),
    [
        (_GEMINI_SHARE, "gemini", "bQBdWIDX3xBr"),
        ("https://gemini.google.com/share/abc123", "gemini", "abc123"),
        (_CHATGPT_SHARE, "chatgpt", "2f4c0a1e-0000"),
        ("https://chat.openai.com/share/legacy", "chatgpt", "legacy"),
        (_CLAUDE_SHARE, "claude", "9c1f0000"),
    ],
)
def test_normalize_share_link_recognises_supported_surfaces(url, provider, share_id):
    link = normalize_share_link(url)

    assert isinstance(link, ShareLink)
    assert link.provider == provider
    assert link.share_id == share_id


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article",
        "https://chatgpt.com/",
        "https://claude.ai/chat/abc",
        "https://share.gemini.google/",
        "https://gemini.google.com/",
        "not a url",
        "",
    ],
)
def test_normalize_share_link_rejects_non_share_urls(url):
    assert normalize_share_link(url) is None


def test_render_hosts_include_the_gemini_cross_domain_redirect():
    hosts = share_render_hosts(_GEMINI_SHARE)

    assert "share.gemini.google" in hosts
    assert "gemini.google.com" in hosts


def test_plain_text_labels_are_not_proof_of_shell_ownership():
    cleaned = normalize_share_text(_GEMINI_RENDER)

    assert cleaned == _GEMINI_RENDER.strip()
    assert "reciprocal rank fusion" in cleaned


def test_plain_text_cleanup_keeps_an_interior_mention_of_sign_in():
    text = "You\nCan you explain what Sign in means here?\nClaude\nIt is an authentication prompt."

    assert "Sign in" in normalize_share_text(text)


def test_share_access_failure_reports_login_redirect_and_chrome_only():
    assert share_access_failure("https://claude.ai/login", "Sign in")
    assert share_access_failure(
        "https://accounts.google.com/ServiceLogin", "Sign in"
    )
    assert share_access_failure("https://gemini.google.com/share/x", "Sign in")
    assert share_access_failure("https://gemini.google.com/share/x", _GEMINI_RENDER) is None


# A long rendered login page: enough prose to defeat the short-text login
# heuristic, and none of the phrases the prompt heuristic matches, so only the
# redirect path can identify it.  Mirrors a provider that bounces a share
# render onto its own auth host while keeping the conversation URL in the
# address bar.
_LONG_LOGIN_RENDER = "\n".join(
    [
        "Sign in",
        "Claude",
        "Authentication is required before the requested resource is served.",
        "The form below collects a credential and challenges it once; after a "
        "successful response the browser is returned to the page that was "
        "requested. Enterprise sessions may delegate the challenge to an "
        "external identity provider instead, and a passkey can replace the "
        "password step entirely where the device supports it.",
        "Already have access? Use the button above to continue.",
        "Sign in",
    ]
)


@pytest.mark.parametrize(
    "url",
    [
        "https://claude.ai/auth/login",
        "https://chatgpt.com/auth/login",
        "https://gemini.google.com/auth/login",
        "https://claude.ai/auth/",
        "https://claude.ai/AUTH/Login",
        "https://claude.ai/login",
        "https://claude.ai/signin",
        "https://claude.ai/sign-in",
        "https://claude.ai/u/login",
        "https://accounts.google.com/signin/v2/identifier",
        "https://auth.openai.com/log-in/password",
    ],
)
def test_share_access_failure_recognises_nested_auth_paths(url):
    assert (
        share_access_failure(url, _LONG_LOGIN_RENDER)
        == "share_link_not_public: login redirect"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://gemini.google.com/share/bQBdWIDX3xBr",
        "https://chatgpt.com/share/2f4c0a1e-0000",
        "https://claude.ai/share/login",
        "https://gemini.google.com/share/abc/login",
        "https://claude.ai/authorize",
        "https://claude.ai/auth0",
        "https://claude.ai/u/",
        "https://claude.ai/help/login",
    ],
)
def test_share_access_failure_keeps_share_identifiers_out_of_auth_paths(url):
    conversation = "\n".join(
        [
            "You",
            "Explain reciprocal rank fusion.",
            "Gemini",
            "Reciprocal rank fusion combines several ranked lists into one ranking by "
            "summing one over a damping constant plus the rank of each document in "
            "every list that returned it. Documents that a list omits contribute "
            "nothing, so agreement across lists raises a document in the fused order.",
            "You",
            "Give a two line summary.",
            "Gemini",
            "It merges multiple rankings into a single score per document, and higher "
            "agreement across lists moves a document up while the damping constant "
            "keeps any single ranking from dominating.",
        ]
    )

    assert share_access_failure(url, conversation) is None


def test_long_login_render_at_unchanged_share_url_is_rejected():
    assert share_access_failure("https://claude.ai/share/x", _LONG_LOGIN_RENDER, access_wall=True)


@pytest.mark.parametrize("text", ["Privacy", "Help", "Claude", "Sign in", "User\nExplain login\nClaude\nThis conversation explains login."])
def test_transcript_edge_labels_and_incidental_login_are_preserved(text):
    assert normalize_share_text(text) == text
    if "User" in text:
        assert share_access_failure(_CLAUDE_SHARE, text) is None


def test_public_share_does_not_bypass_existing_browser_authority():
    result = require_browser_policy(_share_request(_GEMINI_SHARE), None)
    assert not isinstance(result, BrowserAdmission)
    assert result.code == "browser_policy_unavailable"


def _fake_share_browser(final_url: str, rendered_text: str):
    page = MagicMock()
    page.goto = AsyncMock()
    page.url = final_url
    page.title = AsyncMock(return_value="Shared conversation")
    page.evaluate = AsyncMock(return_value={"text": rendered_text, "access_wall": False})
    page.wait_for_load_state = AsyncMock()
    page.close = AsyncMock()

    context = MagicMock()
    context.route = MagicMock()
    context.route_web_socket = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()

    browser = MagicMock()
    browser.is_connected.return_value = True
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    return browser, page


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "final_url", "rendered", "marker"),
    _CANARIES,
)
async def test_share_render_fixture_preserves_transcript(
    monkeypatch, url, final_url, rendered, marker
):
    import argus.extraction.playwright_extractor as extractor

    set_browser_attestation_provider(_Provider(_attestation()))
    browser, page = _fake_share_browser(final_url, rendered)
    monkeypatch.setattr(extractor, "_browser", browser)
    monkeypatch.setattr(extractor, "is_safe_url", lambda _url: (True, ""))
    monkeypatch.setattr(extractor, "SHARE_RENDER_SETTLE_SECONDS", 0)
    monkeypatch.setattr(extractor, "_using_obscura_cdp", False)

    result = await extractor.extract_share_link(url)

    assert result.error is None, result.error
    assert result.extractor == ExtractorName.PLAYWRIGHT
    assert marker in result.text
    assert result.text == rendered.strip()
    page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_share_render_reports_login_wall_never_content(monkeypatch):
    import argus.extraction.playwright_extractor as extractor

    set_browser_attestation_provider(_Provider(_attestation()))
    browser, _page = _fake_share_browser("https://claude.ai/login", "Sign in")
    monkeypatch.setattr(extractor, "_browser", browser)
    monkeypatch.setattr(extractor, "is_safe_url", lambda _url: (True, ""))
    monkeypatch.setattr(extractor, "SHARE_RENDER_SETTLE_SECONDS", 0)
    monkeypatch.setattr(extractor, "_using_obscura_cdp", False)

    result = await extractor.extract_share_link(_CLAUDE_SHARE)

    assert result.text == ""
    assert result.error is not None
    assert "share_link_not_public" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "final_url",
    ["https://claude.ai/auth/login", "https://chatgpt.com/auth/login"],
)
async def test_share_render_long_login_page_at_nested_auth_path(monkeypatch, final_url):
    """A long rendered login page is reported as not public, never as content.

    The provider bounces the render onto a nested auth path on the share host
    itself, so neither an auth host nor a short render catches it.
    """
    import argus.extraction.playwright_extractor as extractor

    set_browser_attestation_provider(_Provider(_attestation()))
    browser, _page = _fake_share_browser(final_url, _LONG_LOGIN_RENDER)
    monkeypatch.setattr(extractor, "_browser", browser)
    monkeypatch.setattr(extractor, "is_safe_url", lambda _url: (True, ""))
    monkeypatch.setattr(extractor, "SHARE_RENDER_SETTLE_SECONDS", 0)
    monkeypatch.setattr(extractor, "_using_obscura_cdp", False)

    result = await extractor.extract_share_link(_CLAUDE_SHARE)

    assert result.text == ""
    assert result.error is not None
    assert "share_link_not_public" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "rendered", "marker"),
    [
        (_GEMINI_SHARE, _GEMINI_RENDER, "reciprocal rank fusion"),
        (_CHATGPT_SHARE, _CHATGPT_RENDER, "circuit breaker"),
    ],
)
async def test_share_chain_routes_to_browser_not_static_extractors(
    monkeypatch, url, rendered, marker
):
    from argus.extraction import extractor as extraction_module

    extraction_module._cache.clear()
    extraction_module._domain_limiter.clear()

    trafilatura = AsyncMock(
        return_value=ExtractedContent(
            url=url,
            text="Sign in",
            word_count=2,
            extractor=ExtractorName.TRAFILATURA,
        )
    )
    monkeypatch.setattr(extraction_module, "_extract_trafilatura", trafilatura)
    monkeypatch.setattr(extraction_module, "is_safe_url", lambda _url: (True, ""))

    rendered_result = ExtractedContent(
        url=url,
        title="Shared conversation",
        text=normalize_share_text(rendered),
        word_count=len(normalize_share_text(rendered).split()),
        extractor=ExtractorName.PLAYWRIGHT,
    )
    browser_render = AsyncMock(return_value=rendered_result)
    monkeypatch.setattr(
        "argus.extraction.playwright_extractor.extract_share_link", browser_render
    )

    result = await extraction_module._extract_url_unpersisted(
        url,
        allow_legacy_cache=False,
    )

    assert result.extractor == ExtractorName.PLAYWRIGHT
    assert marker in result.text
    browser_render.assert_awaited_once()
    trafilatura.assert_not_awaited()


@pytest.mark.asyncio
async def test_share_chain_reports_browser_failure_without_static_chrome(monkeypatch):
    from argus.extraction import extractor as extraction_module

    extraction_module._cache.clear()
    extraction_module._domain_limiter.clear()

    trafilatura = AsyncMock(
        return_value=ExtractedContent(
            url=_GEMINI_SHARE,
            text="Sign in",
            word_count=2,
            extractor=ExtractorName.TRAFILATURA,
        )
    )
    monkeypatch.setattr(extraction_module, "_extract_trafilatura", trafilatura)
    monkeypatch.setattr(extraction_module, "is_safe_url", lambda _url: (True, ""))
    monkeypatch.setattr(
        "argus.extraction.playwright_extractor.extract_share_link",
        AsyncMock(
            return_value=ExtractedContent(
                url=_GEMINI_SHARE,
                error="browser_policy_unavailable: browser request rejected",
            )
        ),
    )

    result = await extraction_module._extract_url_unpersisted(
        _GEMINI_SHARE,
        allow_legacy_cache=False,
    )

    assert result.text == ""
    assert "browser_policy_unavailable" in result.error
    trafilatura.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("during", ["settle", "extraction"])
async def test_delayed_share_login_navigation_never_returns_content(monkeypatch, during):
    import argus.extraction.playwright_extractor as extractor

    set_browser_attestation_provider(_Provider(_attestation()))
    browser, page = _fake_share_browser(_CLAUDE_SHARE, _LONG_LOGIN_RENDER)
    async def navigate(*args, **kwargs):
        page.url = "https://claude.ai/auth/login"
        return {"text": _LONG_LOGIN_RENDER, "access_wall": True}
    if during == "settle":
        page.wait_for_load_state.side_effect = navigate
    else:
        page.evaluate.side_effect = navigate
    monkeypatch.setattr(extractor, "_browser", browser)
    monkeypatch.setattr(extractor, "is_safe_url", lambda _url: (True, ""))
    monkeypatch.setattr(extractor, "SHARE_RENDER_SETTLE_SECONDS", 0)
    monkeypatch.setattr(extractor, "_using_obscura_cdp", False)
    result = await extractor.extract_share_link(_CLAUDE_SHARE)
    assert result.text == ""
    assert "share_link_not_public" in result.error
    page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_delayed_share_destination_is_revalidated_and_attributed(monkeypatch):
    import argus.extraction.playwright_extractor as extractor

    set_browser_attestation_provider(_Provider(_attestation()))
    browser, page = _fake_share_browser(_GEMINI_SHARE, _GEMINI_RENDER)
    target = "https://gemini.google.com/share/bQBdWIDX3xBr"
    async def navigate(*args, **kwargs):
        page.url = target
    page.wait_for_load_state.side_effect = navigate
    checked = []
    def safe(url):
        checked.append(url)
        return True, ""
    monkeypatch.setattr(extractor, "_browser", browser)
    monkeypatch.setattr(extractor, "is_safe_url", safe)
    monkeypatch.setattr(extractor, "SHARE_RENDER_SETTLE_SECONDS", 0)
    monkeypatch.setattr(extractor, "_using_obscura_cdp", False)
    result = await extractor.extract_share_link(_GEMINI_SHARE)
    assert result.error is None
    assert result.url == target
    assert target in checked


def test_transcript_words_and_speaker_names_never_establish_an_access_wall():
    text = "Human\nWhat does the form say?\nClaude\nSign in to continue."
    assert share_access_failure(_CLAUDE_SHARE, text) is None
    assert share_access_failure(_CLAUDE_SHARE, "User\nSign in to continue", access_wall=True)


@pytest.mark.asyncio
async def test_real_dom_share_text_preserves_visible_blocks_and_semantics():
    from playwright.async_api import async_playwright, Error
    from argus.extraction.playwright_extractor import _SHARE_RENDER_SCRIPT

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("optional local Chromium binary unavailable")
            raise
        try:
            page = await browser.new_page()
            await page.set_content("""<nav>Outside shell</nav><main>
                <h2>Human</h2><p>first</p><p>second</p>
                <header>Transcript header</header><aside>Transcript aside</aside>
                <footer>Privacy</footer><p hidden>Hidden secret</p>
                <p style="display:none">Hidden panel</p><p>Sign in to continue</p>
                </main>""")
            rendered = await page.evaluate(_SHARE_RENDER_SCRIPT)
            text = rendered["text"]
            assert "first\n" in text and "second" in text
            for value in ("Human", "Transcript header", "Transcript aside", "Privacy", "Sign in to continue"):
                assert value in text
            assert "Hidden" not in text and "Outside shell" not in text
            assert rendered["access_wall"] is False
            await page.set_content("""<main><h1>Sign in</h1><p>User</p>
                <form action="/auth/login"><input type="password"></form></main>""")
            rendered = await page.evaluate(_SHARE_RENDER_SCRIPT)
            assert rendered["access_wall"] is True
            await page.set_content("""<main><h1>Human</h1><p>Sign in to continue</p>
                <form style="display:none"><input type="password"></form></main>""")
            assert (await page.evaluate(_SHARE_RENDER_SCRIPT))["access_wall"] is False
            await page.set_content("""<main hidden>Hidden stale transcript</main>
                <article><p>Actual public conversation</p></article>""")
            rendered = await page.evaluate(_SHARE_RENDER_SCRIPT)
            assert rendered["text"] == "Actual public conversation"
            await page.set_content("<main hidden>Hidden only transcript</main>")
            assert "Hidden" not in (await page.evaluate(_SHARE_RENDER_SCRIPT))["text"]
            await page.set_content("""<main><p>Stale transcript</p></main>
                <div role="dialog"><h1>Sign in</h1><form action="/auth/login">
                <input type="password"></form></div>""")
            assert (await page.evaluate(_SHARE_RENDER_SCRIPT))["access_wall"] is True
            await page.set_content("""<main><h1>Access denied</h1>
                <p>This page explains that the conversation is unavailable.</p></main>""")
            assert (await page.evaluate(_SHARE_RENDER_SCRIPT))["access_wall"] is True
            await page.set_content("""<main><article><h1>Access denied</h1>
                <p>Human asks about this quoted error message.</p></article></main>""")
            assert (await page.evaluate(_SHARE_RENDER_SCRIPT))["access_wall"] is False
        finally:
            await browser.close()
