import asyncio

import pytest

from argus.broker.guard import (
    GuardAction,
    GuardConfig,
    RunawayGuard,
    normalize_query,
    validate_model_url,
)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def make(**overrides):
    clock = Clock()
    cfg = GuardConfig(**overrides)
    return RunawayGuard(cfg, clock=clock), clock


def test_normalization_keeps_symbols():
    assert normalize_query("  C++  Tips ") == "c++ tips"
    assert normalize_query("C++") != normalize_query("C#")


def test_repeated_query_degrades_to_free_only():
    guard, _ = make(repeat_limit=3)
    actions = [guard.admit("p", "same query").action for _ in range(5)]
    assert actions[:3] == [GuardAction.ALLOW] * 3
    assert actions[3:] == [GuardAction.FREE_ONLY] * 2


def test_distinct_symbol_queries_do_not_share_repeat_limit():
    guard, _ = make(repeat_limit=1)
    assert guard.admit("p", "C++").action is GuardAction.ALLOW
    assert guard.admit("p", "C#").action is GuardAction.ALLOW


def test_burst_degrades_then_recovers():
    guard, clock = make(burst_limit=5, degrade_s=900)
    for i in range(5):
        assert guard.admit("p", f"q{i}").action is GuardAction.ALLOW
    assert guard.admit("p", "q5").action is GuardAction.FREE_ONLY
    assert guard.admit("p", "unrelated").action is GuardAction.FREE_ONLY
    clock.now += 1000  # past degrade window and burst window
    assert guard.admit("p", "later").action is GuardAction.ALLOW


def test_daily_cap_denies():
    guard, clock = make(daily_limit=4, burst_limit=100)
    for i in range(4):
        assert guard.admit("p", f"q{i}").action is GuardAction.ALLOW
    denied = guard.admit("p", "q4")
    assert denied.action is GuardAction.DENY
    # Oldest call was at t0, so the cap lifts one day after it.
    assert denied.retry_after_s == 24 * 3600 + 1
    clock.now += 25 * 3600
    assert guard.admit("p", "q5").action is GuardAction.ALLOW


def test_principals_are_independent():
    guard, _ = make(repeat_limit=1)
    assert guard.admit("a", "x").action is GuardAction.ALLOW
    assert guard.admit("b", "x").action is GuardAction.ALLOW


def test_disabled_guard_allows_everything():
    guard, _ = make(enabled=False, repeat_limit=1)
    assert all(guard.admit("p", "x").action is GuardAction.ALLOW for _ in range(10))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1/chat/completions",
        "http://10.0.0.5/v1",
        "http://localhost:8080",
    ],
)
def test_model_url_accepts_private(url):
    validate_model_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8/v1",
        "https://api.example.com/v1",
        "ftp://127.0.0.1",
        "",
        "http://169.254.169.254/latest",
        "http://[fe80::1]/v1",
        "http://0.0.0.0/v1",
    ],
)
def test_model_url_rejects_public_or_invalid(url):
    with pytest.raises(ValueError):
        validate_model_url(url)


def _trip(guard):
    decision = None
    for i in range(3):
        decision = guard.admit("p", f"q{i}")
    return decision


@pytest.mark.asyncio
async def test_legitimate_verdict_only_widens_burst_limit():
    async def reviewer(recent):
        assert len(recent) <= 20
        return False

    clock = Clock()
    guard = RunawayGuard(
        GuardConfig(burst_limit=2, repeat_limit=1, daily_limit=50),
        reviewer=reviewer,
        clock=clock,
    )
    assert guard.admit("p", "a").action is GuardAction.ALLOW
    assert guard.admit("p", "b").action is GuardAction.ALLOW
    tripped = guard.admit("p", "c")
    assert tripped.action is GuardAction.FREE_ONLY and tripped.needs_review
    await guard.review("p")
    # Burst limit is now 4: the next call is allowed again.
    assert guard.admit("p", "d").action is GuardAction.ALLOW
    # The repeat rule still applies despite the exception.
    guard.admit("p", "same")
    assert guard.admit("p", "same").action is GuardAction.FREE_ONLY


@pytest.mark.asyncio
async def test_failed_review_fails_closed():
    async def reviewer(recent):
        raise RuntimeError("model down")

    guard = RunawayGuard(GuardConfig(burst_limit=1), reviewer=reviewer, clock=Clock())
    guard.admit("p", "a")
    assert guard.admit("p", "b").needs_review
    await guard.review("p")
    assert guard.admit("p", "c").action is GuardAction.FREE_ONLY


@pytest.mark.asyncio
async def test_concurrent_admission_is_bounded():
    guard, _ = make(repeat_limit=3, burst_limit=1000)
    results = await asyncio.gather(
        *(asyncio.to_thread(guard.admit, "p", "same") for _ in range(100))
    )
    allowed = [r for r in results if r.action is GuardAction.ALLOW]
    assert len(allowed) == 3


@pytest.mark.asyncio
async def test_model_client_ignores_environment_proxies(monkeypatch):
    import httpx

    from argus.broker import guard as guard_module

    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json):
            class Response:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"choices": [{"message": {"content": '{"runaway": false}'}}]}

            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    review = guard_module._http_reviewer("http://127.0.0.1:11434/v1")
    assert await review([{"query": "q", "age_s": 1}]) is False
    assert seen["trust_env"] is False


@pytest.mark.asyncio
async def test_rejected_session_request_does_not_consume_guard_quota():
    from unittest.mock import MagicMock

    from argus.api.schemas import SearchRequest
    from argus.operations.accepted import (
        AcceptedOperationRegistration,
        AcceptedOperationService,
    )

    session_authority = MagicMock()
    session_authority.owns.return_value = False
    guard = RunawayGuard(GuardConfig(daily_limit=1))
    service = AcceptedOperationService(
        broker_provider=lambda: MagicMock(),
        repository_provider=MagicMock(),
        session_authority=session_authority,
        registration=AcceptedOperationRegistration.complete(),
        guard=guard,
    )

    for index in range(5):
        operation = await service.search(
            SearchRequest(query=f"q{index}", session_id="not-mine"),
            principal="maya",
            request_id=f"request-{index}",
            require_owned_session=True,
        )
        assert operation.error.code == "session_not_found"

    # None of the rejected requests counted, so the one-call daily cap is intact.
    from argus.broker.guard import GuardAction

    assert guard.admit("maya", "real").action is GuardAction.ALLOW


def test_idle_principals_are_evicted():
    guard, clock = make()
    guard.admit("gone", "x")
    clock.now += 25 * 3600
    guard.admit("active", "y")
    assert "gone" not in guard._callers
    assert "active" in guard._callers
