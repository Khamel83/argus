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
    strip_share_chrome,
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


def test_strip_share_chrome_removes_edge_navigation_but_keeps_conversation():
    cleaned = strip_share_chrome(_GEMINI_RENDER)

    assert "Sign in" not in cleaned
    assert "Cookie settings" not in cleaned
    assert "Terms of Service" not in cleaned
    assert "reciprocal rank fusion" in cleaned


def test_strip_share_chrome_keeps_an_interior_mention_of_sign_in():
    text = "You\nCan you explain what Sign in means here?\nClaude\nIt is an authentication prompt."

    assert "Sign in" in strip_share_chrome(text)


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


def test_long_login_render_without_an_auth_path_is_not_rejected():
    # Guards the regression above: the same long render is not caught by the
    # short-text or prompt heuristics, so only the path rule classifies it.
    assert (
        share_access_failure("https://claude.ai/share/x", _LONG_LOGIN_RENDER) is None
    )


def test_share_browser_exception_is_off_by_default():
    failure = require_browser_policy(_share_request(_GEMINI_SHARE), None)

    assert not isinstance(failure, BrowserAdmission)
    assert failure.code == "browser_policy_unavailable"


def test_share_browser_exception_admits_allowlisted_share_host(monkeypatch):
    monkeypatch.setenv("ARGUS_SHARE_BROWSER_DOMAINS", " share.gemini.google ")
    now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)

    admission = require_browser_policy(_share_request(_GEMINI_SHARE), None, now=now)

    assert isinstance(admission, BrowserAdmission)
    assert admission.basis == "share_browser_exception"
    assert admission.exception_ref == "ARGUS_SHARE_BROWSER_DOMAINS:share.gemini.google"
    assert admission.attestation is None
    assert admission.expires_at == now + timedelta(minutes=5)


@pytest.mark.parametrize(
    ("url", "profile"),
    [
        # Allowlisted host, but not a share link.
        ("https://share.gemini.google/", OriginProfile.PUBLIC_CONTENT),
        # Recognised share link, but the host is not allowlisted.
        ("https://chatgpt.com/share/abc", OriginProfile.PUBLIC_CONTENT),
        # Share host under the authenticated profile keeps fail-closed.
        (_GEMINI_SHARE, OriginProfile.AUTHENTICATED_CONTENT),
        # Lookalike hosts.
        ("https://share.gemini.google.attacker.example/x", OriginProfile.PUBLIC_CONTENT),
        ("https://evilshare.gemini.google/x", OriginProfile.PUBLIC_CONTENT),
    ],
)
def test_share_browser_exception_stays_within_its_scope(monkeypatch, url, profile):
    monkeypatch.setenv("ARGUS_SHARE_BROWSER_DOMAINS", "share.gemini.google,gemini.google.com")
    request = make_browser_request(
        url,
        profile=profile,
        credential_policy="none",
        request_id="share-test",
    )

    result = require_browser_policy(request, None)

    assert not isinstance(result, BrowserAdmission)
    assert result.code == "browser_policy_unavailable"


def test_share_browser_exception_never_overrides_a_supplied_authority(monkeypatch):
    monkeypatch.setenv("ARGUS_SHARE_BROWSER_DOMAINS", "share.gemini.google")

    rejected = require_browser_policy(
        _share_request(_GEMINI_SHARE), _attestation(verified=False)
    )
    attested = require_browser_policy(_share_request(_GEMINI_SHARE), _attestation())

    assert rejected.code == "browser_policy_unavailable"
    assert isinstance(attested, BrowserAdmission)
    assert attested.basis == "attestation"


@pytest.mark.asyncio
async def test_share_browser_exception_admits_gemini_redirect_from_initial_allowlist(
    monkeypatch,
):
    """The initial share host allowlist also covers Gemini's canonical hop."""
    monkeypatch.setenv("ARGUS_SHARE_BROWSER_DOMAINS", "share.gemini.google")
    admission = require_browser_policy(_share_request(_GEMINI_SHARE), None)
    assert isinstance(admission, BrowserAdmission)

    redirect = SimpleNamespace(
        request=SimpleNamespace(
            url="https://gemini.google.com/share/bQBdWIDX3xBr",
            resource_type="document",
        ),
        continue_=AsyncMock(),
        abort=AsyncMock(),
    )
    await guard_browser_route(
        redirect,
        admission,
        url_checker=lambda _url: (True, ""),
    )

    redirect.continue_.assert_awaited_once()
    redirect.abort.assert_not_awaited()


@pytest.mark.asyncio
async def test_share_admission_admits_cross_domain_redirect_only(monkeypatch):
    monkeypatch.setenv("ARGUS_SHARE_BROWSER_DOMAINS", "share.gemini.google,gemini.google.com")
    admission = require_browser_policy(_share_request(_GEMINI_SHARE), None)
    assert isinstance(admission, BrowserAdmission)

    def checker(_url):
        return True, ""

    redirect = SimpleNamespace(
        request=SimpleNamespace(
            url="https://gemini.google.com/share/bQBdWIDX3xBr",
            resource_type="document",
        ),
        continue_=AsyncMock(),
        abort=AsyncMock(),
    )
    await guard_browser_route(redirect, admission, url_checker=checker)
    redirect.continue_.assert_awaited_once()

    foreign = SimpleNamespace(
        request=SimpleNamespace(
            url="https://attacker.example/collect", resource_type="document"
        ),
        continue_=AsyncMock(),
        abort=AsyncMock(),
    )
    await guard_browser_route(foreign, admission, url_checker=checker)
    foreign.abort.assert_awaited_once()
    foreign.continue_.assert_not_awaited()
    assert admission.blocked_resources[-1][2] == (
        "share document navigation outside the declared hosts"
    )


def _fake_share_browser(final_url: str, rendered_text: str):
    page = MagicMock()
    page.goto = AsyncMock()
    page.url = final_url
    page.title = AsyncMock(return_value="Shared conversation")
    page.evaluate = AsyncMock(return_value=rendered_text)
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
async def test_share_render_canary_strips_chrome_and_keeps_transcript(
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
    assert "Sign in" not in result.text
    assert "Log in" not in result.text
    assert "Cookie" not in result.text
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
        text=strip_share_chrome(rendered),
        word_count=len(strip_share_chrome(rendered).split()),
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
