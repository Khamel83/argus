"""Pluggable summarization helpers for workflow reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

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


def get_summarizer() -> BaseSummarizer:
    fallback = ExtractiveSummarizer()
    if get_config().valyu.api_key:
        return ValyuSummarizer(fallback=fallback)
    return fallback
