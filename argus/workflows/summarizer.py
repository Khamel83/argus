"""Pluggable summarization helpers for workflow reports."""

from __future__ import annotations

import os
import re
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
            marker = (
                " [PARTIAL CONTENT]"
                if doc.metadata.get("artifact_disposition") == "partial"
                else ""
            )
            if lead:
                overview_bits.append(f"{marker.strip()} {lead}".strip())
            highlights.append(
                f"- **{doc.title or doc.url}**{marker}: "
                f"{lead or 'Captured and stored for review.'}"
            )

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
            f"- {doc.title or doc.url} ({doc.url})"
            + (
                " [PARTIAL CONTENT]"
                if doc.metadata.get("artifact_disposition") == "partial"
                else ""
            )
            for doc in list(documents)[:6]
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
        result = await valyu_answer(
            query, system_instructions=instructions, fast_mode=True
        )
        if result.error or not result.answer.strip():
            logger.info(
                "Valyu summarizer unavailable, using extractive fallback: %s",
                result.error,
            )
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


_GROUNDED_PROMPT = (
    "You are a research assistant. Given search results and a user query, "
    "produce a concise, well-structured answer. Cite sources by [Source N]. "
    "If the results don't answer the query, say so clearly."
)
_EXPAND_PROMPT = (
    _GROUNDED_PROMPT
    + " Answer from the provided sources first. If you can also search or browse "
    "the web yourself, do additional research to fill gaps or check claims. "
    "Put anything that does not come from the provided sources, including any "
    "extra URLs you found, only under a final heading '## Additional leads'. "
    "Always end with that heading; write 'None' under it if there is nothing "
    "to add. Do not cite [Source N] for those items."
)
_LEADS_HEADING = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*|\*\*)?additional[ \t]+leads\b[ \t]*:?[ \t]*(?:\*\*)?",
    re.IGNORECASE | re.MULTILINE,
)
_NO_LEADS = {"none", "n/a", "na", "nothing", "no additional leads"}


class LLMSummarizer(BaseSummarizer):
    """Synthesize an answer from captured sources through an OpenAI-compatible gateway.

    Built for g2k (``ARGUS_GATEWAY_URL=http://<host>:4312/v1``, model ``auto``)
    but works with any gateway that implements ``/chat/completions``. It never
    blocks the workflow: any gateway failure falls back to the deterministic
    extractive summary.

    ``ARGUS_GATEWAY_MODE``:
    - ``grounded`` (default): answer only from the captured sources.
    - ``expand``: the model may also do its own web research (for example a
      g2k ChatGPT/Gemini web lane). Anything beyond the captured sources is
      returned in a separate, unverified "Additional leads" section.
    """

    def __init__(
        self,
        gateway_url: str = "",
        gateway_key: str = "",
        *,
        model: str = "",
        mode: str = "",
        timeout: float | None = None,
    ):
        self.gateway_url = (gateway_url or os.getenv("ARGUS_GATEWAY_URL", "")).rstrip(
            "/"
        )
        self.gateway_key = gateway_key or os.getenv("ARGUS_GATEWAY_KEY", "")
        self.model = model or os.getenv("ARGUS_GATEWAY_MODEL", "") or "auto"
        resolved_mode = (
            mode or os.getenv("ARGUS_GATEWAY_MODE", "") or "grounded"
        ).lower()
        self.mode = (
            resolved_mode if resolved_mode in {"grounded", "expand"} else "grounded"
        )
        if timeout is None:
            try:
                timeout = float(os.getenv("ARGUS_GATEWAY_TIMEOUT", "") or 120)
            except ValueError:
                timeout = 120.0
        self.timeout = timeout if timeout > 0 else 120.0
        self._fallback = ExtractiveSummarizer()

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
        try:
            answer = await self._ask_gateway(prompt, documents)
        except (
            httpx.HTTPError,
            RuntimeError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
        ) as exc:
            logger.warning(
                "gateway summarizer unavailable (%s: %s); using extractive fallback",
                type(exc).__name__,
                exc,
            )
            return await self._fallback.summarize(
                title=title, prompt=prompt, documents=documents, citations=citations
            )
        if not answer.strip():
            logger.warning(
                "gateway returned an empty answer; using extractive fallback"
            )
            return await self._fallback.summarize(
                title=title, prompt=prompt, documents=documents, citations=citations
            )

        citation_ids = [c.id for c in citations[:10]]
        if self.mode == "expand":
            parts = _LEADS_HEADING.split(answer, maxsplit=1)
            if len(parts) != 2:
                # The model did not separate its own research from the sources.
                # Fail closed: nothing here may carry captured-source citations.
                return [
                    SummarySection(
                        heading="Answer (unverified: source separation failed)",
                        body=answer,
                        citation_ids=[],
                    )
                ]
            leads = parts[1].strip()
            sections = [
                SummarySection(
                    heading="Answer",
                    body=parts[0].strip(),
                    citation_ids=citation_ids,
                )
            ]
            if leads and leads.strip(" .*_`-").lower() not in _NO_LEADS:
                sections.append(
                    SummarySection(
                        heading="Additional leads (unverified)",
                        body=leads,
                        citation_ids=[],
                    )
                )
            return sections
        return [
            SummarySection(heading="Answer", body=answer, citation_ids=citation_ids)
        ]

    async def _ask_gateway(
        self, prompt: str, documents: Sequence[StoredDocument]
    ) -> str:
        if not self.gateway_url:
            raise RuntimeError("ARGUS_GATEWAY_URL is not configured")
        context_parts = []
        for i, doc in enumerate(documents[:20]):
            text = doc.metadata.get("lead_text", "") or ""
            if not text:
                continue
            marker = (
                " [PARTIAL CONTENT]"
                if doc.metadata.get("artifact_disposition") == "partial"
                else ""
            )
            context_parts.append(
                f"[Source {i + 1}]{marker} {doc.title or doc.url}\n{text[:2000]}"
            )
        context = "\n\n".join(context_parts)
        system_prompt = _EXPAND_PROMPT if self.mode == "expand" else _GROUNDED_PROMPT
        user_prompt = f"Query: {prompt}\n\nSearch Results:\n{context}"

        headers = {
            "Content-Type": "application/json",
            # Logging-only attribution for g2k; ignored by other gateways.
            "X-G2K-Caller": "argus-workflows",
        }
        if self.gateway_key:
            headers["Authorization"] = f"Bearer {self.gateway_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.gateway_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                },
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"gateway returned {resp.status_code}: {resp.text[:200]}"
            )
        content = resp.json()["choices"][0]["message"]["content"]
        if content is None:
            return ""
        if not isinstance(content, str):
            raise ValueError("gateway content is not a string")
        return content


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
            logger.info(
                "Valyu API key not configured; using fallback extractive summarizer."
            )
            return fallback
    if kind == "llm":
        if not os.getenv("ARGUS_GATEWAY_URL", "").strip():
            logger.info("ARGUS_GATEWAY_URL not set; using extractive summarizer.")
            return fallback
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
