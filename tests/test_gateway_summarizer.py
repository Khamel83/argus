import httpx
import pytest

from argus.workflows.models import CitationRef, StoredDocument
from argus.workflows.summarizer import (
    ExtractiveSummarizer,
    LLMSummarizer,
    get_summarizer,
)


def _docs():
    docs = [
        StoredDocument(
            id=f"D{i}",
            url=f"https://site{i}.example/p",
            title=f"Site {i}",
            artifact_path="x",
            metadata={"lead_text": f"Lead text {i}."},
        )
        for i in range(2)
    ]
    cites = [
        CitationRef(id=f"S{i}", title=f"Site {i}", url=d.url, artifact_path="x")
        for i, d in enumerate(docs)
    ]
    return docs, cites


class _Resp:
    def __init__(self, status=200, content="An answer [Source 1]."):
        self.status_code = status
        self._content = content
        self.text = "body"

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _patch_post(monkeypatch, response=None, error=None, seen=None):
    async def fake_post(self, url, headers=None, json=None):
        if seen is not None:
            seen.update(url=url, headers=headers, json=json)
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


async def _run(summarizer):
    docs, cites = _docs()
    return await summarizer.summarize(
        title="t", prompt="what is x", documents=docs, citations=cites
    )


@pytest.mark.asyncio
async def test_sends_g2k_model_auth_and_caller_header(monkeypatch):
    seen = {}
    _patch_post(monkeypatch, _Resp(), seen=seen)
    summarizer = LLMSummarizer("http://100.0.0.1:4312/v1/", "tok")

    sections = await _run(summarizer)

    assert seen["url"] == "http://100.0.0.1:4312/v1/chat/completions"
    assert seen["json"]["model"] == "auto"
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["headers"]["X-G2K-Caller"] == "argus-workflows"
    assert sections[0].heading == "Answer"
    assert "[Source 1]" in sections[0].body


@pytest.mark.asyncio
async def test_model_comes_from_env(monkeypatch):
    monkeypatch.setenv("ARGUS_GATEWAY_MODEL", "gemini-web/gemini-flash")
    seen = {}
    _patch_post(monkeypatch, _Resp(), seen=seen)

    await _run(LLMSummarizer("http://gw/v1", "k"))

    assert seen["json"]["model"] == "gemini-web/gemini-flash"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"response": _Resp(status=503)},
        {"response": _Resp(content="   ")},
        {"error": httpx.ConnectTimeout("slow")},
        {"error": ValueError("bad json")},
    ],
)
async def test_gateway_failure_falls_back_to_extractive(monkeypatch, kwargs):
    _patch_post(monkeypatch, **kwargs)

    sections = await _run(LLMSummarizer("http://gw/v1", "k"))

    assert sections[0].heading == "Overview"  # extractive output, not an error


@pytest.mark.asyncio
async def test_expand_mode_splits_unverified_leads(monkeypatch):
    seen = {}
    content = (
        "Main answer [Source 1].\n\n## Additional leads\n- https://extra.example/a"
    )
    _patch_post(monkeypatch, _Resp(content=content), seen=seen)

    sections = await _run(LLMSummarizer("http://gw/v1", "k", mode="expand"))

    assert [s.heading for s in sections] == ["Answer", "Additional leads (unverified)"]
    assert "extra.example" in sections[1].body
    assert sections[1].citation_ids == []
    assert "Additional leads" in seen["json"]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_grounded_mode_never_asks_for_extra_research(monkeypatch):
    seen = {}
    _patch_post(monkeypatch, _Resp(), seen=seen)

    await _run(LLMSummarizer("http://gw/v1", "k"))

    assert "Additional leads" not in seen["json"]["messages"][0]["content"]


def test_unconfigured_gateway_uses_extractive(monkeypatch):
    monkeypatch.delenv("ARGUS_GATEWAY_URL", raising=False)
    assert isinstance(get_summarizer("llm"), ExtractiveSummarizer)


def test_configured_gateway_uses_llm_summarizer(monkeypatch):
    monkeypatch.setenv("ARGUS_GATEWAY_URL", "http://gw/v1")
    assert isinstance(get_summarizer("llm"), LLMSummarizer)


class _RawResp(_Resp):
    def __init__(self, content):
        super().__init__()
        self._content = content


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [["a"], {"k": "v"}, 5])
async def test_non_string_content_falls_back(monkeypatch, content):
    _patch_post(monkeypatch, _RawResp(content))

    sections = await _run(LLMSummarizer("http://gw/v1", "k"))

    assert sections[0].heading == "Overview"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "heading", ["## Additional leads:", "### ADDITIONAL LEADS", "**Additional leads**"]
)
async def test_expand_accepts_heading_variants(monkeypatch, heading):
    content = f"Main [Source 1].\n\n{heading}\n- https://extra.example/a"
    _patch_post(monkeypatch, _Resp(content=content))

    sections = await _run(LLMSummarizer("http://gw/v1", "k", mode="expand"))

    assert [s.heading for s in sections] == ["Answer", "Additional leads (unverified)"]


@pytest.mark.asyncio
async def test_expand_missing_heading_fails_closed(monkeypatch):
    _patch_post(monkeypatch, _Resp(content="Answer plus http://x.example [Source 1]"))

    sections = await _run(LLMSummarizer("http://gw/v1", "k", mode="expand"))

    assert len(sections) == 1
    assert "unverified" in sections[0].heading
    assert sections[0].citation_ids == []


@pytest.mark.asyncio
async def test_expand_none_leads_returns_single_answer(monkeypatch):
    _patch_post(
        monkeypatch, _Resp(content="Main [Source 1].\n\n## Additional leads\nNone")
    )

    sections = await _run(LLMSummarizer("http://gw/v1", "k", mode="expand"))

    assert [s.heading for s in sections] == ["Answer"]
    assert sections[0].citation_ids == ["S0", "S1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "heading_line",
    [
        "## Additional leads: https://extra.example/a",
        "**Additional leads:** https://extra.example/a",
    ],
)
async def test_expand_keeps_leads_written_on_the_heading_line(
    monkeypatch, heading_line
):
    _patch_post(monkeypatch, _Resp(content=f"Main [Source 1].\n\n{heading_line}"))

    sections = await _run(LLMSummarizer("http://gw/v1", "k", mode="expand"))

    assert [s.heading for s in sections] == ["Answer", "Additional leads (unverified)"]
    assert "https://extra.example/a" in sections[1].body


@pytest.mark.asyncio
async def test_malformed_gateway_url_falls_back():
    # A non-numeric port raises httpx.InvalidURL inside client.post().
    sections = await _run(LLMSummarizer("http://gateway.example:notaport/v1", "k"))

    assert sections[0].heading == "Overview"
