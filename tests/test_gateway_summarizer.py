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
