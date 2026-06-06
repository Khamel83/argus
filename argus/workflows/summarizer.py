"""Pluggable summarization helpers for workflow reports."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence

import httpx

from argus.config import get_config
from argus.logging import get_logger
from argus.workflows.models import CitationRef, StoredDocument, SummarySection

logger = get_logger("workflows.summarizer")


def _first_sentences(text: str, *, limit: int = 2) -> str:
    sentences = []
    for chunk in text.replace("\n", " ").split(". "):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not chunk.endswith("."):
            chunk += "."
        sentences.append(chunk)
        if len(sentences) >= limit:
            break
    return " ".join(sentences)


class BaseSummarizer:
    async def summarize(
        self,
        *,
        title: str,
        prompt: str,
        documents: Sequence[StoredDocument],
        citations: Sequence[CitationRef],
    ) -> list[SummarySection]:
        raise NotImplementedError


class ExtractiveSummarizer(BaseSummarizer):
    """Simple, deterministic summarizer from extracted source content."""

    async def summarize(
        self,
        *,
        title: str,
        prompt: str,
        documents: Sequence[StoredDocument],
        citations: Sequence[CitationRef],
    ) -> list[SummarySection]:
        if not documents:
            return [
                SummarySection(
                    heading="No Sources Captured",
                    body="Argus could not capture any source documents for this workflow run.",
                    citation_ids=[],
                )
            ]

        top_docs = list(documents[:5])
        overview_bits = []
        highlights = []
        for doc in top_docs:
            lead = doc.metadata.get("lead_text", "")
            if lead:
                overview_bits.append(lead)
            highlights.append(f"- **{doc.title or doc.url}**: {lead or 'Captured and stored for review.'}")

        overview = " ".join(bit for bit in overview_bits[:3] if bit).strip()
        if not overview:
            overview = f"Argus captured {len(documents)} source documents for {title or prompt}."

        citation_ids = [doc.id for doc in top_docs]
        return [
            SummarySection(
                heading="Overview",
                body=overview,
                citation_ids=citation_ids[:3],
            ),
            SummarySection(
                heading="Notable Sources",
                body="\n".join(highlights),
                citation_ids=citation_ids,
            ),
            SummarySection(
                heading="Coverage",
                body=(
                    f"Saved {len(documents)} documents with traceable artifacts. "
                    "Use the references below to inspect the raw captured content."
                ),
                citation_ids=citation_ids[:2],
            ),
        ]


@dataclass
class ValyuSummarizer(BaseSummarizer):
    """Use Valyu when configured, fall back to extractive summaries on failure."""

    fallback: BaseSummarizer

    async def summarize(
        self,
        *,
        title: str,
        prompt: str,
        documents: Sequence[StoredDocument],
        citations: Sequence[CitationRef],
    ) -> list[SummarySection]:
        from argus.providers.valyu_answer import valyu_answer

        source_list = "\n".join(
            f"- {doc.title or doc.url} ({doc.url})" for doc in list(documents)[:6]
        )
        instructions = (
            "Write a concise factual summary grounded in the listed sources. "
            "Do not invent claims that are not clearly supported by the sources."
        )
        query = (
            f"{prompt}\n\n"
            f"Target: {title or prompt}\n"
            f"Ground only in these sources:\n{source_list}"
        )
        result = await valyu_answer(query, system_instructions=instructions, fast_mode=True)
        if result.error or not result.answer.strip():
            logger.info("Valyu summarizer unavailable, using extractive fallback: %s", result.error)
            return await self.fallback.summarize(
                title=title,
                prompt=prompt,
                documents=documents,
                citations=citations,
            )

        top_ids = [doc.id for doc in list(documents)[:4]]
        return [
            SummarySection(
                heading="Summary",
                body=result.answer.strip(),
                citation_ids=top_ids,
            ),
            SummarySection(
                heading="Coverage",
                body=(
                    f"Valyu generated the summary while Argus persisted {len(documents)} "
                    "locally captured source documents for auditability."
                ),
                citation_ids=top_ids[:2],
            ),
        ]


@dataclass
class LLMSummarizer(BaseSummarizer):
    """Uses the gateway LLM to synthesize a structured answer from search results."""

    def __init__(self, gateway_url: str = "", gateway_key: str = ""):
        self.gateway_url = gateway_url or os.getenv("ARGUS_GATEWAY_URL", "")
        self.gateway_key = gateway_key or os.getenv("ARGUS_GATEWAY_KEY", "")

    async def summarize(
        self,
        *,
        title: str,
        prompt: str,
        documents: Sequence[StoredDocument],
        citations: Sequence[CitationRef],
    ) -> list[SummarySection]:
        if not documents:
            return [
                SummarySection(
                    heading="No Results",
                    body="Argus could not find relevant sources for this query.",
                    citation_ids=[],
                )
            ]

        context_parts = []
        for i, doc in enumerate(documents[:20]):
            text = doc.metadata.get("lead_text", "") or ""
            if not text:
                continue
            context_parts.append(f"[Source {i+1}] {doc.title or doc.url}\n{text[:2000]}")
        context = "\n\n".join(context_parts)

        system_prompt = (
            "You are a research assistant. Given search results and a user query, "
            "produce a concise, well-structured answer. Cite sources by [Source N]. "
            "If the results don't answer the query, say so clearly."
        )
        user_prompt = f"Query: {prompt}\n\nSearch Results:\n{context}"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.gateway_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.gateway_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "cheap",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"gateway returned {resp.status_code}: {resp.text[:200]}")
            body = resp.json()
            answer = body.get("choices", [{}])[0].get("message", {}).get("content", "")

        return [
            SummarySection(heading="Answer", body=answer, citation_ids=[c.id for c in citations[:10]]),
        ]


def get_summarizer(kind: str = "extractive") -> BaseSummarizer:
    """Return a summarizer instance based on the requested ``kind``.

    Parameters
    ----------
    kind: str
        The summarizer type. Supported values are:
        - "extractive": Simple deterministic summarizer.
        - "valyu": Uses Valyu service when an API key is configured; otherwise falls back.
        - "llm": Uses the gateway LLM for synthesis.

    The function always provides an ``ExtractiveSummarizer`` as a fallback
    to guarantee a usable summarizer even when the requested kind cannot be
    instantiated (e.g., missing configuration).
    """
    fallback = ExtractiveSummarizer()
    kind = kind.lower()
    if kind == "valyu":
        if get_config().valyu.api_key:
            return ValyuSummarizer(fallback=fallback)
        else:
            logger.info("Valyu API key not configured; using fallback extractive summarizer.")
            return fallback
    if kind == "llm":
        return LLMSummarizer()
    if kind == "extractive":
        return ExtractiveSummarizer()
    # Unknown kind – default to fallback
    logger.warning(f"Unknown summarizer kind '{kind}'; using fallback.")
    return fallback


_SUMMARIZERS: dict[str, type[BaseSummarizer]] = {
    "extractive": ExtractiveSummarizer,
    "valyu": ValyuSummarizer,
    "llm": LLMSummarizer,
}
